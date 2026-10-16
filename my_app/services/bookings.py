"""Reservation-only booking services. SportMate has no payment processing."""
import secrets
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from time import sleep

from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from my_app.models import Court, CourtBlockedPeriod, CourtBooking, Game, Venue
from my_app.services.availability import ALLOWED_SLOT_DURATIONS, BOOKING_BLOCKING_STATUSES, VenueAvailabilityService


class BookingError(APIException):
    status_code = 400
    default_code = 'BOOKING_ERROR'
    default_detail = 'The booking could not be created.'


class BookingConflict(BookingError):
    status_code = 409
    default_code = 'SLOT_UNAVAILABLE'
    default_detail = 'This slot is no longer available. Please choose another slot.'


class BookingPricingService:
    """Authoritative pricing; callers cannot supply or modify calculated fields."""
    @staticmethod
    def calculate(*, court, starts_at, ends_at):
        duration_hours = Decimal(str((ends_at - starts_at).total_seconds())) / Decimal('3600')
        base_price = (court.price_per_hour * duration_hours).quantize(Decimal('0.01'))
        additional_fees = Decimal('0.00')
        discount = Decimal('0.00')
        final_amount = max(Decimal('0.00'), base_price + additional_fees - discount)
        return {
            'base_price': base_price,
            'additional_fees': additional_fees,
            'discount': discount,
            'final_amount': final_amount,
        }


class BookingStatusService:
    VALID_TRANSITIONS = {
        CourtBooking.Status.PENDING: {CourtBooking.Status.CONFIRMED, CourtBooking.Status.CANCELLED},
        CourtBooking.Status.CONFIRMED: {CourtBooking.Status.CANCELLED, CourtBooking.Status.COMPLETED},
    }

    @classmethod
    def transition(cls, *, booking, target_status):
        if target_status not in cls.VALID_TRANSITIONS.get(booking.status, set()):
            raise BookingError(f'Cannot change booking from {booking.status} to {target_status}.')
        booking.status = target_status
        booking.save(update_fields=['status', 'updated_at'])
        return booking

    @classmethod
    def complete_expired(cls, *, now=None):
        now = now or timezone.now()
        return CourtBooking.objects.filter(
            status=CourtBooking.Status.CONFIRMED, ends_at__lte=now,
        ).update(status=CourtBooking.Status.COMPLETED, updated_at=now)


class BookingCancellationService:
    @classmethod
    @transaction.atomic
    def cancel(cls, *, booking, actor, reason=''):
        booking = CourtBooking.objects.select_for_update().select_related('venue').get(pk=booking.pk)
        if booking.user_id != actor.id and not actor.is_staff:
            raise PermissionDenied('You may only cancel your own booking.')
        if booking.status not in (CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED):
            raise BookingError('This booking cannot be cancelled.')
        policy = getattr(booking.venue, 'cancellation_policy', None)
        if policy and policy.active:
            if not policy.allow_cancellation:
                raise BookingError('Cancellation is not permitted by this venue policy.')
            cutoff = timezone.now() + timedelta(hours=policy.minimum_cancellation_hours)
            if booking.starts_at <= cutoff:
                raise BookingError('This booking is outside the venue cancellation window.')
        booking.status = CourtBooking.Status.CANCELLED
        booking.cancellation_reason = reason.strip()
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=['status', 'cancellation_reason', 'cancelled_at', 'updated_at'])
        from my_app.services.notifications import NotificationService
        if booking.venue.owner_id and booking.venue.owner_id != actor.id:
            NotificationService.create_notification(
                user=booking.venue.owner, actor=actor, type='booking_cancelled',
                title='Booking cancelled', body=f'{actor.username} cancelled booking {booking.booking_reference}.',
                data={'booking_id': str(booking.public_id), 'venue_id': str(booking.venue.public_id)},
                event_key=f'booking-cancelled:{booking.pk}:{booking.updated_at.isoformat()}:owner:{booking.venue.owner_id}',
            )
        return booking


class BookingService:
    @staticmethod
    def _reference():
        return f'SM-{secrets.token_urlsafe(5).upper().replace("_", "X").replace("-", "Y")}'[:16]

    @classmethod
    def _local_datetimes(cls, *, venue, booking_date, start_time, end_time):
        if booking_date < timezone.localdate():
            raise ValidationError({'booking_date': ['Past dates cannot be booked.']})
        if end_time <= start_time:
            raise ValidationError({'end_time': ['end_time must be after start_time.']})
        local_open, local_close = VenueAvailabilityService.local_range(venue, booking_date)
        zone = VenueAvailabilityService.venue_zone(venue)
        local_start = timezone.make_aware(datetime.combine(booking_date, start_time), zone)
        local_end = timezone.make_aware(datetime.combine(booking_date, end_time), zone)
        if local_start <= timezone.now():
            raise BookingError('Past times cannot be booked.')
        if local_start < local_open or local_end > local_close:
            raise BookingError('Requested time is outside venue operating hours.')
        return local_start.astimezone(datetime_timezone.utc), local_end.astimezone(datetime_timezone.utc)

    @classmethod
    def _validate_request(cls, *, venue, court, booking_date, start_time, end_time, lock_bookings=False):
        if not venue.active or venue.is_demo:
            raise BookingError('Venue is inactive or is not a real bookable venue.')
        if court.venue_id != venue.id:
            raise BookingError('Court does not belong to the selected venue.')
        if not court.active:
            raise BookingError('Court is inactive.')
        starts_at, ends_at = cls._local_datetimes(
            venue=venue, booking_date=booking_date, start_time=start_time, end_time=end_time,
        )
        duration_minutes = int((ends_at - starts_at).total_seconds() / 60)
        if duration_minutes not in ALLOWED_SLOT_DURATIONS:
            raise ValidationError({'end_time': [f'Duration must be one of {ALLOWED_SLOT_DURATIONS} minutes.']})
        if CourtBlockedPeriod.objects.filter(court=court, starts_at__lt=ends_at, ends_at__gt=starts_at).exists():
            raise BookingConflict('The court is blocked or under maintenance.')
        conflicts = CourtBooking.objects.filter(
            court=court, status__in=BOOKING_BLOCKING_STATUSES,
            starts_at__lt=ends_at, ends_at__gt=starts_at,
        )
        if lock_bookings:
            conflicts = conflicts.select_for_update()
        if conflicts.exists():
            raise BookingConflict()
        game_conflicts = Game.objects.filter(
            court=court,
            status__in=(Game.Status.OPEN, Game.Status.ALMOST_FULL, Game.Status.FULL, Game.Status.STARTED),
            starts_at__lt=ends_at,
            ends_at__gt=starts_at,
        )
        if lock_bookings:
            game_conflicts = game_conflicts.select_for_update()
        if game_conflicts.exists():
            raise BookingConflict('The court is reserved for a game during this time.')
        return starts_at, ends_at

    @classmethod
    def quote(cls, *, venue_id, court_id, booking_date, start_time, end_time):
        try:
            venue = Venue.objects.get(public_id=venue_id)
        except Venue.DoesNotExist as exc:
            raise NotFound('Venue not found.') from exc
        try:
            court = Court.objects.select_related('venue').get(public_id=court_id)
        except Court.DoesNotExist as exc:
            raise NotFound('Court not found.') from exc
        starts_at, ends_at = cls._validate_request(
            venue=venue, court=court, booking_date=booking_date, start_time=start_time, end_time=end_time,
        )
        prices = BookingPricingService.calculate(court=court, starts_at=starts_at, ends_at=ends_at)
        return venue, court, prices

    @classmethod
    def create(cls, *, user, venue_id, court_id, booking_date, start_time, end_time, status=CourtBooking.Status.CONFIRMED):
        for attempt in range(5):
            try:
                return cls._create_once(
                    user=user, venue_id=venue_id, court_id=court_id,
                    booking_date=booking_date, start_time=start_time,
                    end_time=end_time, status=status,
                )
            except OperationalError as exc:
                locked = 'locked' in str(exc).lower()
                if connection.vendor != 'sqlite' or not locked:
                    raise
                if attempt == 4:
                    raise BookingConflict('This slot is being booked right now. Refresh availability and try again.') from exc
                sleep(0.05 * (attempt + 1))

    @classmethod
    @transaction.atomic
    def _create_once(cls, *, user, venue_id, court_id, booking_date, start_time, end_time, status=CourtBooking.Status.CONFIRMED):
        if connection.vendor == 'sqlite':
            # This must be the first database operation in the transaction:
            # SQLite otherwise upgrades two concurrent read transactions to
            # writers at once and can let both proceed from a stale snapshot.
            Court.objects.filter(public_id=court_id).update(updated_at=F('updated_at'))
        try:
            venue = Venue.objects.select_for_update().get(public_id=venue_id)
        except Venue.DoesNotExist as exc:
            raise NotFound('Venue not found.') from exc
        try:
            court = Court.objects.select_for_update().get(public_id=court_id)
        except Court.DoesNotExist as exc:
            raise NotFound('Court not found.') from exc
        if not venue.active or venue.is_demo:
            raise BookingError('Venue is inactive or is not a real bookable venue.')
        if court.venue_id != venue.id:
            raise BookingError('Court does not belong to the selected venue.')
        if not court.active:
            raise BookingError('Court is inactive.')
        starts_at, ends_at = cls._local_datetimes(
            venue=venue, booking_date=booking_date, start_time=start_time, end_time=end_time,
        )
        # Treat a retried request as successful only when the same player
        # already holds that exact active reservation. Other users and
        # overlapping requests still go through the conflict check below.
        existing = CourtBooking.objects.select_for_update().filter(
            user=user, court=court, starts_at=starts_at, ends_at=ends_at,
            status__in=BOOKING_BLOCKING_STATUSES,
        ).first()
        if existing:
            return existing
        starts_at, ends_at = cls._validate_request(
            venue=venue, court=court, booking_date=booking_date, start_time=start_time, end_time=end_time,
            lock_bookings=True,
        )
        prices = BookingPricingService.calculate(court=court, starts_at=starts_at, ends_at=ends_at)
        for _ in range(5):
            try:
                booking = CourtBooking.objects.create(
                    user=user, venue=venue, court=court, booking_date=booking_date,
                    start_time=start_time, end_time=end_time, starts_at=starts_at, ends_at=ends_at,
                    booking_reference=cls._reference(), status=status, **prices,
                )
                from my_app.services.notifications import NotificationService
                booking_data={'booking_id': str(booking.public_id), 'venue_id': str(venue.public_id)}
                NotificationService.create_notification(
                    user=user, type='booking_created', title='Booking confirmed',
                    body=f'Your booking at {venue.name} is {booking.status}.', data=booking_data,
                    event_key=f'booking-created:{booking.pk}:player:{user.pk}',
                )
                if venue.owner_id and venue.owner_id != user.id:
                    NotificationService.create_notification(
                        user=venue.owner, actor=user, type='booking_created', title='New court booking',
                        body=f'{user.username} booked {court.name} at {venue.name}.', data=booking_data,
                        event_key=f'booking-created:{booking.pk}:owner:{venue.owner_id}',
                    )
                return booking
            except IntegrityError:
                continue
        raise BookingError('Could not allocate a unique booking reference.')

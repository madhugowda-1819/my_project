from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from my_app.models import Court, CourtBlockedPeriod, CourtBooking, Venue


ALLOWED_SLOT_DURATIONS = (30, 60, 90, 120)
BOOKING_BLOCKING_STATUSES = (CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED)


class SlotUnavailable(APIException):
    status_code = 409
    default_code = 'SLOT_UNAVAILABLE'
    default_detail = 'The selected court slot is no longer available.'


class VenueAvailabilityService:
    @staticmethod
    def venue_zone(venue):
        try:
            return ZoneInfo(venue.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValidationError({'venue': ['Venue timezone is invalid.']}) from exc

    @classmethod
    def local_range(cls, venue, date_value):
        zone = cls.venue_zone(venue)
        start = timezone.make_aware(datetime.combine(date_value, venue.opening_time), zone)
        end = timezone.make_aware(datetime.combine(date_value, venue.closing_time), zone)
        return start, end

    @staticmethod
    def overlaps(start, end, existing_start, existing_end):
        return existing_start < end and existing_end > start

    @classmethod
    def slots(cls, *, venue, date_value, sport=None, court_id=None, duration=60):
        if duration not in ALLOWED_SLOT_DURATIONS:
            raise ValidationError({'duration': [f'Duration must be one of {ALLOWED_SLOT_DURATIONS} minutes.']})
        local_open, local_close = cls.local_range(venue, date_value)
        courts = venue.courts.select_related('sport').filter(active=True)
        if sport:
            courts = courts.filter(sport=sport)
        if court_id:
            courts = courts.filter(pk=court_id)
        court_list = list(courts)
        if court_id and not court_list:
            raise ValidationError({'court': ['Court is unavailable for this venue and filter.']})
        utc_open, utc_close = local_open.astimezone(datetime_timezone.utc), local_close.astimezone(datetime_timezone.utc)
        bookings = CourtBooking.objects.filter(
            court__in=court_list, status__in=BOOKING_BLOCKING_STATUSES,
            starts_at__lt=utc_close, ends_at__gt=utc_open,
        )
        periods = CourtBlockedPeriod.objects.filter(court__in=court_list, starts_at__lt=utc_close, ends_at__gt=utc_open)
        bookings_by_court = {court.pk: [] for court in court_list}
        periods_by_court = {court.pk: [] for court in court_list}
        for booking in bookings:
            bookings_by_court[booking.court_id].append(booking)
        for period in periods:
            periods_by_court[period.court_id].append(period)
        output = []
        for court in court_list:
            cursor = local_open
            while cursor + timedelta(minutes=duration) <= local_close:
                end = cursor + timedelta(minutes=duration)
                utc_start, utc_end = cursor.astimezone(datetime_timezone.utc), end.astimezone(datetime_timezone.utc)
                status = 'available' if venue.active and court.active else 'closed'
                if status == 'available' and any(cls.overlaps(utc_start, utc_end, booking.starts_at, booking.ends_at) for booking in bookings_by_court[court.pk]):
                    status = 'booked'
                if status == 'available':
                    matching_periods = [period for period in periods_by_court[court.pk] if cls.overlaps(utc_start, utc_end, period.starts_at, period.ends_at)]
                    if any(period.type == CourtBlockedPeriod.Type.MAINTENANCE for period in matching_periods):
                        status = 'maintenance'
                    elif matching_periods:
                        status = 'blocked'
                output.append({
                    'court': court, 'date': date_value.isoformat(), 'start_time': cursor.timetz().replace(tzinfo=None),
                    'end_time': end.timetz().replace(tzinfo=None), 'price': (court.price_per_hour * Decimal(duration) / Decimal(60)),
                    'status': status, 'starts_at': utc_start, 'ends_at': utc_end,
                })
                cursor = end
        return output

    @classmethod
    @transaction.atomic
    def create_booking(cls, *, user, court, starts_at, ends_at):
        Court.objects.select_for_update().get(pk=court.pk)
        if not court.active or not court.venue.active:
            raise SlotUnavailable('The court or venue is closed.')
        conflicts = CourtBooking.objects.select_for_update().filter(
            court=court, status__in=BOOKING_BLOCKING_STATUSES,
            starts_at__lt=ends_at, ends_at__gt=starts_at,
        )
        if conflicts.exists() or CourtBlockedPeriod.objects.filter(court=court, starts_at__lt=ends_at, ends_at__gt=starts_at).exists():
            raise SlotUnavailable()
        return CourtBooking.objects.create(user=user, court=court, starts_at=starts_at, ends_at=ends_at, status=CourtBooking.Status.PENDING)

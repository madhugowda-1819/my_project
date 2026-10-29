"""Venue-owner portal controllers; shared records remain in ``my_app``."""
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from my_app.models import Court, CourtBlockedPeriod, CourtBooking, Game, Venue
from .models import VenueOwnerApplication
from my_app.permissions import IsActiveAccount
from .permissions import IsApprovedVenueOwner
from my_app.serializers import CourtSerializer, VenueSerializer
from .serializers import (VenueOwnerBlockedPeriodWriteSerializer,
    VenueOwnerCourtWriteSerializer, VenueOwnerRegistrationSerializer,
    VenueOwnerVenueWriteSerializer)
from my_app.services.accounts import AuthenticationService
from my_app.services.games import ACTIVE_GAME_STATUSES


class VenueOwnerRegisterView(APIView):
    permission_classes = [AllowAny]; throttle_classes = [ScopedRateThrottle]; throttle_scope = 'registration'
    def post(self, request):
        serializer = VenueOwnerRegistrationSerializer(data=request.data); serializer.is_valid(raise_exception=True); data = serializer.validated_data
        with transaction.atomic():
            user = AuthenticationService.register(data=data, create_player_profile=False)
            application = VenueOwnerApplication.objects.create(applicant=user, business_name=data['business_name'].strip(), phone=data['phone'].strip(), city=data['city'].strip(), address=data['address'].strip())
        return Response({'success': True, 'message': 'Venue-owner account created. Your application is pending admin approval.', 'application': {'public_id': str(application.public_id), 'status': application.status}}, status=201)


class VenueOwnerApplicationView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        items = request.user.venue_owner_applications.order_by('-created_at')
        return Response({'success': True, 'data': [{'public_id': str(item.public_id), 'business_name': item.business_name, 'status': item.status, 'review_note': item.review_note} for item in items]})

    def post(self, request):
        required = ('business_name', 'phone', 'city', 'address')
        missing = [field for field in required if not str(request.data.get(field, '')).strip()]
        if missing:
            raise ValidationError({field: ['This field is required.'] for field in missing})
        if VenueOwnerApplication.objects.filter(
            applicant=request.user,
            status__in=[VenueOwnerApplication.Status.PENDING, VenueOwnerApplication.Status.APPROVED],
        ).exists():
            raise ValidationError({'detail': 'You already have a pending or approved venue-owner application.'})
        item = VenueOwnerApplication.objects.create(applicant=request.user, **{field: str(request.data[field]).strip() for field in required})
        return Response({'success': True, 'data': {'public_id': str(item.public_id), 'status': item.status}}, status=201)


class VenueOwnerDashboardView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get(self, request):
        venues = Venue.objects.filter(owner=request.user)
        venue_ids = venues.values('pk')
        bookings = CourtBooking.objects.filter(venue_id__in=venue_ids)
        now = timezone.now()
        upcoming = bookings.filter(status__in=[CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED], ends_at__gt=now)
        collected = bookings.filter(status__in=[CourtBooking.Status.CONFIRMED, CourtBooking.Status.COMPLETED]).aggregate(total=Sum('final_amount'))['total'] or 0
        recent = bookings.select_related('venue', 'court').order_by('-created_at')[:8]
        activity = [{'type': 'booking', 'title': 'Booking received', 'detail': f'{item.venue.name} · {item.court.name}', 'status': item.status, 'timestamp': item.created_at} for item in recent]
        return Response({'success': True, 'data': {
            'venues': venues.count(), 'active_venues': venues.filter(active=True).count(), 'pending_venues': venues.filter(active=False).count(),
            'courts': Court.objects.filter(venue__in=venue_ids).count(), 'active_courts': Court.objects.filter(venue__in=venue_ids, active=True).count(),
            'upcoming_bookings': upcoming.count(), 'bookings_total': bookings.count(),
            'completed_bookings': bookings.filter(status=CourtBooking.Status.COMPLETED).count(), 'cancelled_bookings': bookings.filter(status=CourtBooking.Status.CANCELLED).count(),
            'collected_revenue': str(collected), 'recent_activity': activity,
            'summary': {'total_venues': venues.count(), 'active_courts': Court.objects.filter(venue__in=venue_ids, active=True).count(), 'upcoming_bookings': upcoming.count(), 'pending_venues': venues.filter(active=False).count()},
            'business_insights': {'total_bookings': bookings.count(), 'completed_bookings': bookings.filter(status=CourtBooking.Status.COMPLETED).count(), 'cancelled_bookings': bookings.filter(status=CourtBooking.Status.CANCELLED).count(), 'collected_revenue': str(collected)},
        }})


class VenueOwnerReportsView(APIView):
    """Owner-scoped operational metrics, based solely on booking records.

    Utilization is booked confirmed/completed court-hours divided by the
    opening-hours capacity of active courts in the requested inclusive range.
    """
    permission_classes = [IsApprovedVenueOwner]

    def get(self, request):
        from_date = request.query_params.get('from')
        to_date = request.query_params.get('to')
        try:
            start = date.fromisoformat(from_date) if from_date else timezone.localdate() - timedelta(days=29)
            end = date.fromisoformat(to_date) if to_date else timezone.localdate()
        except ValueError:
            raise ValidationError({'date_range': 'Use ISO dates: YYYY-MM-DD.'})
        if end < start:
            raise ValidationError({'date_range': 'The end date must not precede the start date.'})
        if (end - start).days > 366:
            raise ValidationError({'date_range': 'Reports support a maximum range of 366 days.'})

        bookings = CourtBooking.objects.filter(
            venue__owner=request.user, booking_date__range=(start, end),
        )
        status_counts = {item['status']: item['count'] for item in bookings.values('status').annotate(count=Count('id'))}
        courts = Court.objects.filter(venue__owner=request.user, active=True).select_related('venue')
        booked_seconds = sum(
            max(0, (booking.ends_at - booking.starts_at).total_seconds())
            for booking in bookings.filter(status__in=[CourtBooking.Status.CONFIRMED, CourtBooking.Status.COMPLETED]).only('starts_at', 'ends_at')
        )
        days = (end - start).days + 1
        capacity_seconds = sum(
            max(0, (court.venue.closing_time.hour * 3600 + court.venue.closing_time.minute * 60) -
                (court.venue.opening_time.hour * 3600 + court.venue.opening_time.minute * 60)) * days
            for court in courts
        )
        by_day = list(bookings.values('booking_date').annotate(count=Count('id')).order_by('booking_date'))
        return Response({'success': True, 'data': {
            'from': start, 'to': end, 'bookings_total': bookings.count(),
            'status_counts': {status: status_counts.get(status, 0) for status in CourtBooking.Status.values},
            'cancelled_bookings': status_counts.get(CourtBooking.Status.CANCELLED, 0),
            'booked_hours': round(booked_seconds / 3600, 2),
            'utilization_percent': round((booked_seconds / capacity_seconds * 100) if capacity_seconds else 0, 2),
            'bookings_by_date': by_day,
        }})


class VenueOwnerVenuesView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get(self, request):
        venues = Venue.objects.filter(owner=request.user).prefetch_related('venue_sports__sport', 'courts__sport').order_by('name')
        return Response({'success': True, 'data': VenueSerializer(venues, many=True, context={'request': request}).data})
    @transaction.atomic
    def post(self, request):
        serializer = VenueOwnerVenueWriteSerializer(data=request.data); unknown = set(request.data) - set(serializer.fields)
        if unknown: raise ValidationError({field: 'This field cannot be changed by venue owners.' for field in sorted(unknown)})
        serializer.is_valid(raise_exception=True); venue = serializer.save(owner=request.user, active=False)
        return Response({'success': True, 'detail': 'Venue submitted for superowner review. It will become bookable after approval.', 'data': VenueSerializer(venue, context={'request': request}).data}, status=201)


class VenueOwnerVenueDetailView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def patch(self, request, public_id):
        venue = get_object_or_404(Venue, public_id=public_id, owner=request.user); serializer = VenueOwnerVenueWriteSerializer(venue, data=request.data, partial=True); unknown = set(request.data) - set(serializer.fields)
        if unknown: raise ValidationError({field: 'This field cannot be changed by venue owners.' for field in sorted(unknown)})
        serializer.is_valid(raise_exception=True); venue = serializer.save()
        return Response({'success': True, 'data': VenueSerializer(venue, context={'request': request}).data})


class VenueOwnerCourtsView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get_venue(self, public_id): return get_object_or_404(Venue, public_id=public_id, owner=self.request.user)
    def get(self, request, public_id):
        courts = self.get_venue(public_id).courts.select_related('sport').order_by('name'); return Response({'success': True, 'data': CourtSerializer(courts, many=True).data})
    @transaction.atomic
    def post(self, request, public_id):
        venue = self.get_venue(public_id); serializer = VenueOwnerCourtWriteSerializer(data=request.data); serializer.is_valid(raise_exception=True)
        if not venue.venue_sports.filter(sport=serializer.validated_data['sport']).exists(): raise ValidationError({'sport_id': 'Add this sport to the venue profile before creating a court.'})
        return Response({'success': True, 'data': CourtSerializer(serializer.save(venue=venue)).data}, status=201)


class VenueOwnerCourtDetailView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get_court(self, request, public_id): return get_object_or_404(Court.objects.select_related('venue', 'sport'), public_id=public_id, venue__owner=request.user)
    @transaction.atomic
    def patch(self, request, public_id):
        court = get_object_or_404(Court.objects.select_for_update().select_related('venue', 'sport'), public_id=public_id, venue__owner=request.user); payload = request.data.copy(); active = payload.pop('active', None); serializer = VenueOwnerCourtWriteSerializer(court, data=payload, partial=True); serializer.is_valid(raise_exception=True)
        if not court.venue.venue_sports.filter(sport=serializer.validated_data.get('sport', court.sport)).exists(): raise ValidationError({'sport_id': 'Add this sport to the venue profile before assigning it to a court.'})
        court = serializer.save()
        if active is not None:
            if not isinstance(active, bool): raise ValidationError({'active': 'Must be true or false.'})
            if not active and CourtBooking.objects.filter(
                court=court, status__in=[CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED], ends_at__gt=timezone.now(),
            ).exists():
                raise ValidationError({'active': 'This court has upcoming active bookings and cannot be deactivated.'})
            if active and not court.venue.active: raise ValidationError({'active': 'A court cannot be activated while its venue is awaiting approval or inactive.'})
            court.active = active; court.save(update_fields=['active', 'updated_at'])
        return Response({'success': True, 'data': CourtSerializer(court).data})
    @transaction.atomic
    def delete(self, request, public_id):
        court = get_object_or_404(Court.objects.select_for_update(), public_id=public_id, venue__owner=request.user)
        if CourtBooking.objects.filter(
            court=court, status__in=[CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED], ends_at__gt=timezone.now(),
        ).exists():
            raise ValidationError({'detail': 'This court has upcoming active bookings and cannot be deactivated.'})
        court.active = False; court.save(update_fields=['active', 'updated_at']); return Response({'success': True, 'detail': 'Court deactivated; booking history was preserved.'})


class VenueOwnerBookingsView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get(self, request):
        queryset = CourtBooking.objects.filter(venue__owner=request.user).select_related('venue', 'court', 'court__sport', 'user').order_by('-starts_at', '-pk'); status = request.query_params.get('status')
        if status:
            if status not in CourtBooking.Status.values: raise ValidationError({'status': 'Choose a valid booking status.'})
            queryset = queryset.filter(status=status)
        if request.query_params.get('venue'): queryset = queryset.filter(venue__public_id=request.query_params['venue'])
        if request.query_params.get('court'): queryset = queryset.filter(court__public_id=request.query_params['court'])
        search = request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(Q(booking_reference__icontains=search) | Q(user__username__icontains=search))
        for parameter, lookup in (('from', 'booking_date__gte'), ('to', 'booking_date__lte')):
            value = request.query_params.get(parameter)
            if value:
                try:
                    queryset = queryset.filter(**{lookup: date.fromisoformat(value)})
                except ValueError:
                    raise ValidationError({parameter: 'Use an ISO date: YYYY-MM-DD.'})
        try:
            page_size = min(max(int(request.query_params.get('page_size', 20)), 1), 100)
        except (TypeError, ValueError):
            page_size = 20
        page = request.query_params.get('page')
        def serialize(items):
            return [{'public_id': str(x.public_id), 'booking_reference': x.booking_reference, 'venue_id': str(x.venue.public_id), 'venue_name': x.venue.name, 'court_name': x.court.name, 'sport': x.court.sport.name, 'customer': x.user.username, 'booking_date': x.booking_date, 'start_time': x.start_time, 'end_time': x.end_time, 'starts_at': x.starts_at, 'ends_at': x.ends_at, 'status': x.status, 'amount': x.final_amount} for x in items]
        if page:
            from django.core.paginator import Paginator
            paginator = Paginator(queryset, page_size); page_obj = paginator.get_page(page); data = serialize(page_obj.object_list)
            return Response({'success': True, 'count': paginator.count, 'next': page_obj.next_page_number() if page_obj.has_next() else None, 'previous': page_obj.previous_page_number() if page_obj.has_previous() else None, 'data': data, 'results': data})
        return Response({'success': True, 'data': serialize(queryset[:200])})


class VenueOwnerBlockedPeriodsView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get_court(self, request, venue_id, court_id): return get_object_or_404(Court.objects.select_related('venue'), public_id=court_id, venue__public_id=venue_id, venue__owner=request.user)
    def get(self, request, venue_id, court_id):
        periods = self.get_court(request, venue_id, court_id).blocked_periods.order_by('-starts_at')[:200]; return Response({'success': True, 'data': [{'public_id': str(x.public_id), 'starts_at': x.starts_at, 'ends_at': x.ends_at, 'type': x.type, 'reason': x.reason} for x in periods]})
    @transaction.atomic
    def post(self, request, venue_id, court_id):
        court = get_object_or_404(Court.objects.select_for_update().select_related('venue'), public_id=court_id, venue__public_id=venue_id, venue__owner=request.user); serializer = VenueOwnerBlockedPeriodWriteSerializer(data=request.data); serializer.is_valid(raise_exception=True); start, end = serializer.validated_data['starts_at'], serializer.validated_data['ends_at']
        if CourtBlockedPeriod.objects.filter(court=court, starts_at__lt=end, ends_at__gt=start).exists():
            raise ValidationError({'starts_at': 'This period overlaps an existing blocked or maintenance period.'})
        if CourtBooking.objects.filter(court=court, status__in=[CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED], starts_at__lt=end, ends_at__gt=start).exists(): raise ValidationError({'starts_at': 'This period overlaps a customer booking. Resolve the booking before blocking this time.'})
        if Game.objects.filter(court=court, status__in=ACTIVE_GAME_STATUSES, starts_at__lt=end, ends_at__gt=start).exists(): raise ValidationError({'starts_at': 'This period overlaps an active match.'})
        period = serializer.save(court=court); return Response({'success': True, 'data': {'public_id': str(period.public_id), 'starts_at': period.starts_at, 'ends_at': period.ends_at, 'type': period.type, 'reason': period.reason}}, status=201)


class VenueOwnerBlockedPeriodDetailView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def delete(self, request, public_id):
        get_object_or_404(CourtBlockedPeriod, public_id=public_id, court__venue__owner=request.user).delete(); return Response(status=204)

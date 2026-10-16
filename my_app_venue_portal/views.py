"""Venue-owner portal controllers; shared records remain in ``my_app``."""
from django.db import transaction
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
        item = VenueOwnerApplication.objects.create(applicant=request.user, **{field: str(request.data[field]).strip() for field in required})
        return Response({'success': True, 'data': {'public_id': str(item.public_id), 'status': item.status}}, status=201)


class VenueOwnerDashboardView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get(self, request):
        venues = Venue.objects.filter(owner=request.user); venue_ids = venues.values('pk'); bookings = CourtBooking.objects.filter(venue_id__in=venue_ids)
        return Response({'success': True, 'data': {'venues': venues.count(), 'active_venues': venues.filter(active=True).count(), 'pending_venues': venues.filter(active=False).count(), 'courts': Court.objects.filter(venue__in=venue_ids).count(), 'active_courts': Court.objects.filter(venue__in=venue_ids, active=True).count(), 'upcoming_bookings': bookings.filter(status__in=[CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED], ends_at__gt=timezone.now()).count(), 'bookings_total': bookings.count()}})


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
        court = self.get_court(request, public_id); payload = request.data.copy(); active = payload.pop('active', None); serializer = VenueOwnerCourtWriteSerializer(court, data=payload, partial=True); serializer.is_valid(raise_exception=True)
        if not court.venue.venue_sports.filter(sport=serializer.validated_data.get('sport', court.sport)).exists(): raise ValidationError({'sport_id': 'Add this sport to the venue profile before assigning it to a court.'})
        court = serializer.save()
        if active is not None:
            if not isinstance(active, bool): raise ValidationError({'active': 'Must be true or false.'})
            if active and not court.venue.active: raise ValidationError({'active': 'A court cannot be activated while its venue is awaiting approval or inactive.'})
            court.active = active; court.save(update_fields=['active', 'updated_at'])
        return Response({'success': True, 'data': CourtSerializer(court).data})
    @transaction.atomic
    def delete(self, request, public_id):
        court = self.get_court(request, public_id); court.active = False; court.save(update_fields=['active', 'updated_at']); return Response({'success': True, 'detail': 'Court deactivated; booking history was preserved.'})


class VenueOwnerBookingsView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get(self, request):
        queryset = CourtBooking.objects.filter(venue__owner=request.user).select_related('venue', 'court', 'court__sport', 'user').order_by('-starts_at'); status = request.query_params.get('status')
        if status:
            if status not in CourtBooking.Status.values: raise ValidationError({'status': 'Choose a valid booking status.'})
            queryset = queryset.filter(status=status)
        if request.query_params.get('venue'): queryset = queryset.filter(venue__public_id=request.query_params['venue'])
        return Response({'success': True, 'data': [{'public_id': str(x.public_id), 'booking_reference': x.booking_reference, 'venue_id': str(x.venue.public_id), 'venue_name': x.venue.name, 'court_name': x.court.name, 'sport': x.court.sport.name, 'customer': x.user.username, 'booking_date': x.booking_date, 'start_time': x.start_time, 'end_time': x.end_time, 'starts_at': x.starts_at, 'ends_at': x.ends_at, 'status': x.status, 'amount': x.final_amount} for x in queryset[:200]]})


class VenueOwnerBlockedPeriodsView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def get_court(self, request, venue_id, court_id): return get_object_or_404(Court.objects.select_related('venue'), public_id=court_id, venue__public_id=venue_id, venue__owner=request.user)
    def get(self, request, venue_id, court_id):
        periods = self.get_court(request, venue_id, court_id).blocked_periods.order_by('-starts_at')[:200]; return Response({'success': True, 'data': [{'public_id': str(x.public_id), 'starts_at': x.starts_at, 'ends_at': x.ends_at, 'type': x.type, 'reason': x.reason} for x in periods]})
    @transaction.atomic
    def post(self, request, venue_id, court_id):
        court = get_object_or_404(Court.objects.select_for_update().select_related('venue'), public_id=court_id, venue__public_id=venue_id, venue__owner=request.user); serializer = VenueOwnerBlockedPeriodWriteSerializer(data=request.data); serializer.is_valid(raise_exception=True); start, end = serializer.validated_data['starts_at'], serializer.validated_data['ends_at']
        if CourtBooking.objects.filter(court=court, status__in=[CourtBooking.Status.PENDING, CourtBooking.Status.CONFIRMED], starts_at__lt=end, ends_at__gt=start).exists(): raise ValidationError({'starts_at': 'This period overlaps a customer booking. Resolve the booking before blocking this time.'})
        if Game.objects.filter(court=court, status__in=ACTIVE_GAME_STATUSES, starts_at__lt=end, ends_at__gt=start).exists(): raise ValidationError({'starts_at': 'This period overlaps an active match.'})
        period = serializer.save(court=court); return Response({'success': True, 'data': {'public_id': str(period.public_id), 'starts_at': period.starts_at, 'ends_at': period.ends_at, 'type': period.type, 'reason': period.reason}}, status=201)


class VenueOwnerBlockedPeriodDetailView(APIView):
    permission_classes = [IsApprovedVenueOwner]
    def delete(self, request, public_id):
        get_object_or_404(CourtBlockedPeriod, public_id=public_id, court__venue__owner=request.user).delete(); return Response(status=204)

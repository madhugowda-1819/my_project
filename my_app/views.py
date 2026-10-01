from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import JsonResponse
from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import (
    Sport, AvailabilitySlot, Match, Message,
    Notification, Ground
)

from .serializers import (
    RegistrationRequestSerializer, LoginRequestSerializer, CurrentUserSerializer,
    UserSerializer, SportSerializer, PublicPlayerSerializer, UserSportSerializer,
    UserSportWriteSerializer, PlayerProfilePreferencesSerializer,
    NearbySearchSerializer,
    PlayerRecommendationQuerySerializer,
    VenueSerializer, VenueDiscoveryQuerySerializer, VenueReviewCreateSerializer, VenueReviewSerializer,
    VenueAvailabilityQuerySerializer, CourtBookingCreateSerializer,
    BookingCreateSerializer, BookingSerializer,
    MatchSerializer, MessageSerializer,
    NotificationSerializer, AvailabilityToggleSerializer,
    GroundSerializer, MatchCreateSerializer
)
from .services.matches import create_match, join_match
from .services.recommendations import recommended_matches_for
from .services.accounts import AuthenticationService, UserService
from .services.players import PlayerService
from .services.locations import nearby_queryset
from .services.player_matching import PlayerMatchingService
from .services.venues import VenueDiscoveryService, VenueReviewService
from .services.availability import VenueAvailabilityService
from .services.bookings import BookingService, BookingCancellationService
from .models import Venue, CourtBooking, VenueReview
from .permissions import IsActiveAccount

User = get_user_model()


# ---------------- AUTH ----------------
class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'registration'

    def post(self, request):
        serializer = RegistrationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = AuthenticationService.register(data=serializer.validated_data)

        return Response({
            'success': True,
            'user': CurrentUserSerializer(user).data,
            'tokens': AuthenticationService.tokens_for(user),
        }, status=201)


@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def login_view(request):
    serializer = LoginRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    identifier = serializer.validated_data.get('identifier') or serializer.validated_data['email']
    user = AuthenticationService.login(identifier=identifier, password=serializer.validated_data['password'])

    # ✅ mark user online
    return Response({
        'success': True,
        'user': CurrentUserSerializer(user).data,
        'tokens': AuthenticationService.tokens_for(user),
    })


login_view.throttle_scope = 'login'
login_view.cls.throttle_scope = 'login'


class LogoutView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        AuthenticationService.logout(user=request.user, refresh_token=request.data.get('refresh'))
        return Response({'success': True, 'message': 'Logged out.'})
    
# ---------------- FORGOT PASSWORD ----------------
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def forgot_password(request):
    email = request.data.get('email')

    try:
        user = User.objects.get(email__iexact=(email or '').strip())
    except User.DoesNotExist:
        # Do not reveal whether an account exists for a supplied email address.
        return Response({"message": "If an account exists, a reset link has been sent."})

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = PasswordResetTokenGenerator().make_token(user)

    reset_link = f"{settings.PASSWORD_RESET_FRONTEND_URL}/{uid}/{token}"

    send_mail(
        subject="Reset Password - SportMate",
        message=f"Click to reset password: {reset_link}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
    )

    return Response({"message": "If an account exists, a reset link has been sent."})


forgot_password.throttle_scope = 'password_reset'
forgot_password.cls.throttle_scope = 'password_reset'


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request, uidb64=None, token=None):
    new_password = request.data.get('password')
    uidb64 = uidb64 or request.data.get('uid')
    token = token or request.data.get('token')

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return Response({"success": False, "error": {"code": "INVALID_RESET_LINK", "message": "Invalid reset link."}}, status=400)

    if not PasswordResetTokenGenerator().check_token(user, token):
        return Response({"success": False, "error": {"code": "INVALID_RESET_TOKEN", "message": "Invalid or expired reset token."}}, status=400)

    AuthenticationService.set_password(user=user, password=new_password)

    return Response({"success": True, "message": "Password reset successful."})


# ---------------- CHANGE PASSWORD ----------------
@api_view(['POST'])
@permission_classes([IsActiveAccount])
def change_password(request):
    user = request.user

    old_password = request.data.get('old_password')
    new_password = request.data.get('new_password')
    confirm_password = request.data.get('confirm_password')

    if not old_password or not new_password or not confirm_password:
        return Response({"error": "All fields required"}, status=400)

    if not user.check_password(old_password):
        return Response({"error": "Old password incorrect"}, status=400)

    if new_password != confirm_password:
        return Response({"error": "Passwords do not match"}, status=400)

    user.set_password(new_password)
    user.save()

    return Response({"msg": "Password changed successfully"})


# ---------------- USER ----------------
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = CurrentUserSerializer
    permission_classes = [IsActiveAccount]

    def get_object(self):
        return self.request.user

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = UserService.update_profile(user=request.user, validated_data=serializer.validated_data)
        return Response(CurrentUserSerializer(user).data)


class AccountTokenRefreshView(TokenRefreshView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            refresh = RefreshToken(request.data.get('refresh'))
            user = User.objects.get(pk=refresh['user_id'])
        except Exception as exc:
            raise AuthenticationFailed('Invalid refresh token.') from exc
        if not user.is_active or user.account_status != User.AccountStatus.ACTIVE:
            raise AuthenticationFailed('This account is suspended or deactivated.')
        return super().post(request, *args, **kwargs)


class MySportsView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request):
        serializer = UserSportWriteSerializer(data=request.data.get('sports', []), many=True)
        serializer.is_valid(raise_exception=True)
        sports = PlayerService.replace_sports(user=request.user, sports=serializer.validated_data)
        return Response({'success': True, 'sports': UserSportSerializer(sports, many=True).data})


class MyPlayerProfileView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request):
        serializer = PlayerProfilePreferencesSerializer(request.user.profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        profile = PlayerService.update_profile(user=request.user, validated_data=serializer.validated_data)
        return Response({'success': True, 'player_profile': PlayerProfilePreferencesSerializer(profile).data})


class PlayerDetailView(generics.RetrieveAPIView):
    serializer_class = PublicPlayerSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        return User.objects.filter(
            Q(profile__profile_visibility='public') | Q(pk=self.request.user.pk)
        ).prefetch_related('user_sports__sport')


# ---------------- SPORTS ----------------
class SportListView(generics.ListAPIView):
    queryset = Sport.objects.filter(is_active=True)
    serializer_class = SportSerializer
    permission_classes = [AllowAny]
    pagination_class = None  # ← add this line


class SportDetailView(generics.RetrieveAPIView):
    queryset = Sport.objects.filter(is_active=True)
    serializer_class = SportSerializer
    permission_classes = [AllowAny]


# ---------------- AI PLAYER MATCHING ----------------
class PlayerListView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        sport = request.query_params.get('sport')
        try:
            lat = float(request.query_params['lat']) if 'lat' in request.query_params else None
            lng = float(request.query_params['lng']) if 'lng' in request.query_params else None
            radius = float(request.query_params.get('radius', 15))
        except ValueError:
            return Response({'success': False, 'error': {'code': 'VALIDATION_ERROR', 'message': 'lat, lng, and radius must be numbers.'}}, status=400)

        user = request.user

        players = User.objects.filter(is_available=True, profile__profile_visibility='public').exclude(id=user.id).prefetch_related('user_sports__sport')

        if sport:
            players = players.filter(Q(user_sports__sport__slug__iexact=sport) | Q(user_sports__sport__name__iexact=sport))

        results = []

        for p in players.distinct():
            distance = p.distance_to(lat, lng) if lat is not None and lng is not None else None

            if distance is not None and distance > radius:
                continue

            # 🔥 AI MATCHING ALGORITHM
            p.distance_km = distance

            results.append(p)

        results.sort(key=lambda x: x.distance_km if x.distance_km is not None else float('inf'))

        return Response(PublicPlayerSerializer(results, many=True).data)


class NearbyPlayerView(generics.ListAPIView):
    serializer_class = PublicPlayerSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        params = NearbySearchSerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        data = params.validated_data
        queryset = User.objects.filter(
            is_available=True, profile__profile_visibility='public',
        ).exclude(pk=self.request.user.pk).prefetch_related('user_sports__sport')
        if data.get('sport'):
            queryset = queryset.filter(
                Q(user_sports__sport__slug__iexact=data['sport'])
                | Q(user_sports__sport__name__iexact=data['sport'])
            )
        if data.get('skill_level'):
            queryset = queryset.filter(user_sports__skill_level=data['skill_level'])
        return nearby_queryset(queryset.distinct(), **{
            key: data[key] for key in ('latitude', 'longitude', 'radius')
        })


class NearbyVenueView(generics.ListAPIView):
    serializer_class = GroundSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        params = NearbySearchSerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        data = params.validated_data
        queryset = Ground.objects.prefetch_related('sports')
        if data.get('sport'):
            queryset = queryset.filter(
                Q(sports__slug__iexact=data['sport']) | Q(sports__name__iexact=data['sport'])
            ).distinct()
        return nearby_queryset(queryset, **{
            key: data[key] for key in ('latitude', 'longitude', 'radius')
        })


class PlayerRecommendationView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        query = PlayerRecommendationQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        matches = PlayerMatchingService.recommend(
            requester=request.user,
            sport=data['sport'],
            latitude=data['latitude'],
            longitude=data['longitude'],
            radius=data['radius'],
            limit=data['limit'],
        )
        return Response({
            'success': True,
            'algorithm': 'deterministic_weighted_compatibility_v1',
            'results': [
                {
                    'player': PublicPlayerSerializer(item['candidate']).data,
                    'score': item['score'].score,
                    'distance_km': round(item['candidate'].distance_km, 2),
                    'sport': item['sport'].sport.slug,
                    'skill_level': item['sport'].skill_level,
                    'rating': item['sport'].rating,
                    'matching_reasons': item['score'].reasons,
                }
                for item in matches
            ],
        })


class VenueListView(generics.ListAPIView):
    serializer_class = VenueSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return VenueDiscoveryService.filter_and_rank(
            queryset=Venue.objects.all(), data={}, amenities=[],
        )


class VenueDetailView(generics.RetrieveAPIView):
    serializer_class = VenueSerializer
    permission_classes = [AllowAny]
    lookup_field = 'public_id'

    def get_queryset(self):
        return Venue.objects.filter(active=True).prefetch_related(
            'venue_sports__sport', 'courts__sport', 'images', 'reviews__user', 'amenities',
        )


class VenueSearchView(generics.ListAPIView):
    serializer_class = VenueSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        raw = self.request.query_params.copy()
        amenities = [item.strip() for item in raw.get('amenities', '').split(',') if item.strip()]
        if amenities:
            raw.setlist('amenities', amenities)
        query = VenueDiscoveryQuerySerializer(data=raw)
        query.is_valid(raise_exception=True)
        return VenueDiscoveryService.filter_and_rank(
            queryset=Venue.objects.all(), data=query.validated_data, amenities=amenities,
        )


class VenueNearbyView(VenueSearchView):
    """Nearby venue discovery shares filtering/ranking with general venue search."""

    def get_queryset(self):
        coordinates = NearbySearchSerializer(data=self.request.query_params)
        coordinates.is_valid(raise_exception=True)
        return super().get_queryset()


class VenueReviewView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        serializer = VenueReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = get_object_or_404(CourtBooking.objects.select_related('court__venue'), pk=serializer.validated_data['booking_id'])
        review = VenueReviewService.create(
            user=request.user,
            booking=booking,
            rating=serializer.validated_data['rating'],
            comment=serializer.validated_data.get('comment', ''),
        )
        return Response({'success': True, 'review': VenueReviewSerializer(review).data}, status=201)


class VenueReviewDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def delete(self, request, public_id):
        review = get_object_or_404(VenueReview, public_id=public_id)
        VenueReviewService.delete(user=request.user, review=review)
        return Response(status=204)


class VenueAvailabilityView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, public_id):
        venue = get_object_or_404(Venue, public_id=public_id)
        query = VenueAvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        slots = VenueAvailabilityService.slots(
            venue=venue, date_value=data['date'], sport=data.get('sport'),
            court_id=data.get('court'), duration=data['duration'],
        )
        return Response({'success': True, 'venue': str(venue.public_id), 'timezone': venue.timezone, 'slots': [
            {
                'court': {'id': slot['court'].public_id, 'name': slot['court'].name, 'sport': slot['court'].sport.slug},
                'date': slot['date'], 'start_time': slot['start_time'], 'end_time': slot['end_time'],
                'price': slot['price'], 'status': slot['status'],
            } for slot in slots
        ]})


class CourtBookingCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        serializer = CourtBookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = VenueAvailabilityService.create_booking(user=request.user, **serializer.validated_data)
        return Response({'success': True, 'booking': {'public_id': booking.public_id, 'status': booking.status}}, status=201)


class BookingListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsActiveAccount]
    serializer_class = BookingSerializer

    def get_queryset(self):
        queryset = CourtBooking.objects.filter(user=self.request.user).select_related('venue', 'court')
        status = self.request.query_params.get('status')
        date = self.request.query_params.get('date')
        venue = self.request.query_params.get('venue')
        court = self.request.query_params.get('court')
        if status:
            queryset = queryset.filter(status=status)
        if date:
            queryset = queryset.filter(booking_date=date)
        if venue:
            queryset = queryset.filter(venue_id=venue)
        if court:
            queryset = queryset.filter(court_id=court)
        if self.request.query_params.get('upcoming') == 'true':
            queryset = queryset.filter(ends_at__gt=timezone.now())
        if self.request.query_params.get('past') == 'true':
            queryset = queryset.filter(ends_at__lte=timezone.now())
        return queryset.order_by('-starts_at')

    def create(self, request, *args, **kwargs):
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = BookingService.create(user=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': BookingSerializer(booking).data}, status=201)


class BookingQuoteView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        venue, court, prices = BookingService.quote(**serializer.validated_data)
        return Response({
            'success': True,
            'venue_name': venue.name,
            'court_name': court.name,
            'booking_date': serializer.validated_data['booking_date'],
            'start_time': serializer.validated_data['start_time'],
            'end_time': serializer.validated_data['end_time'],
            **prices,
        })


class BookingDetailView(generics.RetrieveAPIView):
    permission_classes = [IsActiveAccount]
    serializer_class = BookingSerializer
    lookup_field = 'public_id'

    def get_queryset(self):
        return CourtBooking.objects.filter(user=self.request.user).select_related('venue', 'court')


class BookingCancelView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        booking = get_object_or_404(CourtBooking.objects.select_related('venue'), public_id=public_id)
        booking = BookingCancellationService.cancel(actor=request.user, booking=booking, reason=request.data.get('reason', ''))
        return Response({'success': True, 'data': BookingSerializer(booking).data})


# ---------------- GROUNDS ----------------
class GroundListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        lat = float(request.GET.get('lat'))
        lng = float(request.GET.get('lng'))

        grounds = Ground.objects.all()
        results = []

        for g in grounds:
            d = g.distance_to(lat, lng)
            if d <= 10:
                g.distance_km = round(d, 2)
                results.append(g)

        results.sort(key=lambda x: x.distance_km)

        return Response(GroundSerializer(results, many=True).data)


# ---------------- MATCHES ----------------
class MatchListView(generics.ListAPIView):
    queryset = Match.objects.all()
    serializer_class = MatchSerializer
    permission_classes = [IsActiveAccount]


class CreateMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        serializer = MatchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        match = create_match(organizer=request.user, validated_data=serializer.validated_data)

        return Response({
            "success": True,
            "message": "Match created",
            "match": MatchSerializer(match).data,
        }, status=201)


class JoinMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, match_id):
        match, joined = join_match(match_id=match_id, user=request.user)
        return Response({
            'success': True,
            'message': 'Joined successfully' if joined else 'Already joined this match.',
            'match': MatchSerializer(match).data,
        })


# ---------------- CHAT ----------------
# ---------------- CHAT ----------------
from .utils import create_notification   # 🔥 add this import


class MessageThreadView(generics.ListAPIView):
    serializer_class = MessageSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        other = self.kwargs['user_id']
        me = self.request.user.id

        messages = Message.objects.filter(
            Q(sender_id=me, receiver_id=other) |
            Q(sender_id=other, receiver_id=me)
        ).order_by('created_at')   # 🔥 important (chat order)

        # ✅ mark messages as read
        Message.objects.filter(
            sender_id=other,
            receiver_id=me,
            is_read=False
        ).update(is_read=True)

        return messages


class MessageCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        receiver_id = request.data.get('receiver')
        content = request.data.get('content')

        if not receiver_id or not content:
            return Response(
                {"error": "receiver and content required"},
                status=400
            )

        try:
            receiver = User.objects.get(id=receiver_id)
        except User.DoesNotExist:
            return Response({"error": "User not found"}, status=404)

        msg = Message.objects.create(
            sender=request.user,
            receiver=receiver,
            content=content,
            is_delivered=True
        )

        # 🔥 use utils instead of raw Notification
        create_notification(
            user=receiver,
            type='message',
            title=f'New message from {request.user.username}',
            body=content[:80],
            data={'message_id': msg.id}
        )

        return Response(MessageSerializer(msg).data)


class UnreadMessageCountView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        count = Message.objects.filter(
            receiver=request.user,
            is_read=False
        ).count()

        return Response({"unread": count})

# ---------------- NOTIFICATIONS ----------------
class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)


# ---------------- AVAILABILITY ----------------
class AvailabilityView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request):
        serializer = AvailabilityToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user

        if 'is_available' in serializer.validated_data:
            user.is_available = serializer.validated_data['is_available']
            user.save()

        if 'slots' in serializer.validated_data:
            AvailabilitySlot.objects.filter(user=user).delete()

            AvailabilitySlot.objects.bulk_create([
                AvailabilitySlot(user=user, **slot)
                for slot in serializer.validated_data['slots']
            ])

        return Response(UserSerializer(user).data)
class AiMatchView(generics.GenericAPIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        matches = recommended_matches_for(request.user)
        page = self.paginate_queryset(matches)
        if page is not None:
            return self.get_paginated_response(MatchSerializer(page, many=True).data)
        return Response(MatchSerializer(matches, many=True).data)


def global_project_homepage(request):
    """Handles the main domain homepage root '/'."""
    return JsonResponse({
        "status": "online",
        "project": "SportMate Backend API Platform",
        "message": "Server initialized and actively handling traffic.",
        "api_v1_root": "/api/v1/",
    })


def api_root_landing(request):
    """Handles the API index root '/api/'."""
    return JsonResponse({
        "status": "online",
        "message": "SportMate REST API Backend is running successfully!",
        "version": "v1",
    })

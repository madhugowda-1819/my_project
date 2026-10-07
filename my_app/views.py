from django.contrib.auth import get_user_model
from django.db.models import Q, Count, F
from django.db import connection
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.conf import settings
from django.core.cache import cache
from urllib.parse import urlencode
import json
import math

import requests

from .models import (
    Sport, AvailabilitySlot, Match, Message, Notification, Ground, SportsGround,
    Venue, Court, VenueReview, CourtBooking, Game, GamePlayer, Conversation, ConversationMember,
    Community, CommunityMember, CommunityPost, CommunityComment, CommunityPostLike,
    Event, EventParticipant, Tournament, TournamentTeam, TournamentMatch, NotificationPreference, UserDevice, PlayerRating, Achievement, UserAchievement, Report, ModerationAction,
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
    GroundSerializer, GroundDiscoverySerializer, AutoGameCreateSerializer, MatchCreateSerializer,
    GameCreateSerializer, GameUpdateSerializer, GameQuerySerializer, GameRecommendationQuerySerializer,
    GameSerializer, GamePlayerSerializer, ConversationCreateSerializer,
    ConversationMessageCreateSerializer, ConversationMessageSerializer,
    CommunityCreateSerializer, CommunityUpdateSerializer, CommunitySerializer, CommunityMemberSerializer,
    CommunityPostCreateSerializer, CommunityContentSerializer, CommunityPostSerializer, CommunityCommentSerializer,
    EventCreateSerializer, EventSerializer, EventParticipantSerializer, TournamentCreateSerializer, TournamentSerializer, TournamentTeamSerializer, TournamentMatchSerializer,
    NotificationPreferenceSerializer, UserDeviceSerializer, UserDeviceCreateSerializer,
    PlayerRatingSerializer, PlayerRatingCreateSerializer,
    AchievementSerializer, UserAchievementSerializer,
    ReportCreateSerializer, ReportSerializer, ReportUpdateSerializer, ModerationActionSerializer, AdminUserSerializer,
)
from .maps import MapsProviderError, find_live_sports_grounds
from .permissions import IsActiveAccount, IsPlatformAdmin, IsPlatformModerator, has_platform_role, MODERATOR_GROUPS
from .services.accounts import AuthenticationService, UserService
from .services.availability import VenueAvailabilityService
from .services.bookings import BookingCancellationService, BookingService
from .services.games import ACTIVE_GAME_STATUSES, GameLifecycleService, GameService
from .services.game_matching import GameMatchingService
from .services.chat import (
    ChatPermissionService, ConversationService, MessageService, UnreadMessageService,
)
from .services.communities import (
    CommunityCommentService, CommunityDiscoveryService, CommunityMembershipService,
    CommunityModerationService, CommunityPostService, CommunityService,
)
from .services.events import EventService, EventRegistrationService, EventCapacityService, TournamentService, TournamentBracketService, TournamentMatchService, TournamentStandingsService
from .services.notifications import NotificationService, DeviceService
from .services.moderation import ModerationService
from .services.statistics import RatingService, PlayerStatisticsService, LeaderboardService
from .services.achievements import AchievementEvaluationService, AchievementService
from .services.search import GlobalSearchService, VALID_TYPES
from .services.recommendation_engine import RecommendationProfileService, RecommendationService
from .services.locations import nearby_queryset
from .services.player_matching import PlayerMatchingService
from .services.players import PlayerService
from .services.venues import VenueDiscoveryService, VenueReviewService
from .services.ground_ingestion import GroundIngestionService
from .services.auto_games import AutoGameService
from .services.matches import join_match
from .utils.geo_utils import clean_google_maps_name, haversine_distance

User = get_user_model()


def global_project_homepage(request):
    return JsonResponse({'name': 'SportMate API', 'status': 'ok', 'api': '/api/v1/'})


def api_root_landing(request):
    return JsonResponse({'name': 'SportMate API', 'version': 'v1', 'status': 'ok'})


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """Non-sensitive liveness/readiness endpoint for deployment monitoring."""
    try:
        connection.ensure_connection()
    except Exception:
        return Response({'status': 'unavailable'}, status=503)
    return Response({'status': 'ok'})


def password_reset_page(request):
    """Browser fallback for reset links opened outside the mobile app."""
    uid = request.GET.get('uid', '')
    token = request.GET.get('token', '')
    if not uid or not token:
        return HttpResponseBadRequest('This password-reset link is incomplete.')
    endpoint = f'/api/reset-password/{uid}/{token}/'
    endpoint_json = json.dumps(endpoint)
    html = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reset password | SportMate</title><style>
:root{--blue:#175cd3;--navy:#102a43;--green:#8eea27;--bg:#f6f8fc;--muted:#667085;--border:#e4e7ec;--success:#12b76a}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 8% 0,#e5f0ff,transparent 32%),var(--bg);color:var(--navy);font-family:Inter,Arial,sans-serif}
.page{width:min(100% - 32px,470px);margin:0 auto;padding:48px 0}.brand{display:flex;align-items:center;justify-content:center;gap:12px;margin-bottom:28px}.brand svg{width:52px;height:52px;filter:drop-shadow(0 7px 12px #175cd333)}.brand-name{font-size:24px;font-weight:800;letter-spacing:-.6px}.brand-name span{color:#65b820}.tagline{margin:2px 0 0;color:var(--muted);font-size:12px;font-weight:600}
.card{background:#fff;border:1px solid #eef1f6;border-radius:22px;padding:32px;box-shadow:0 18px 45px #102a4312}.eyebrow{color:var(--blue);font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.card h1{font-size:30px;line-height:1.15;letter-spacing:-1px;margin:10px 0 12px}.card p{color:var(--muted);line-height:1.55;margin:0 0 24px}label{display:block;color:var(--navy);font-size:14px;font-weight:700;margin:17px 0 7px}input,button{width:100%;font:inherit;border-radius:12px}input{border:1px solid var(--border);padding:14px 15px;font-size:16px;outline:none}input:focus{border-color:var(--blue);box-shadow:0 0 0 4px #175cd31a}button{border:0;background:var(--blue);color:#fff;cursor:pointer;font-weight:800;padding:15px;margin-top:24px;transition:transform .15s,background .15s}button:hover{background:#124db2}button:active{transform:scale(.99)}button:disabled{opacity:.65;cursor:wait}.message{min-height:22px;margin:16px 0 0;color:#d92d20;font-size:14px;font-weight:600}.success{display:none;text-align:center;padding:4px 0 8px}.success-mark{width:72px;height:72px;margin:0 auto 18px;border-radius:50%;display:grid;place-items:center;background:#ecfdf3;color:var(--success);font-size:36px;font-weight:800}.success h1{margin-bottom:12px}.back-note{margin-top:24px;padding:13px;border-radius:12px;background:#f0f6ff;color:#175cd3;font-size:13px;font-weight:700;text-align:center}@media(max-width:420px){.page{padding-top:28px}.card{padding:25px 21px}.card h1{font-size:27px}}
</style></head><body><main class="page"><header class="brand" aria-label="SportMate"><svg viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="logoG" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#b8ff27"/><stop offset="1" stop-color="#22c55e"/></linearGradient></defs><rect width="64" height="64" rx="17" fill="#102a43"/><rect x="3" y="3" width="58" height="58" rx="15" fill="none" stroke="url(#logoG)" stroke-width="2"/><circle cx="32" cy="21" r="9" fill="none" stroke="url(#logoG)" stroke-width="2.5"/><path d="M15 46c5-15 11-19 17-20-2 8-1 15 5 21-8-1-14 0-22-1Z" fill="url(#logoG)"/><path d="M49 46c-5-15-11-19-17-20 2 8 1 15-5 21 8-1 14 0 22-1Z" fill="#fff"/></svg><div><div class="brand-name">Sport<span>Mate</span></div><p class="tagline">Play • Connect • Win</p></div></header><section class="card"><div id="form-state"><div class="eyebrow">Account security</div><h1>Choose a new password</h1><p>Use a strong password you do not use on another service.</p><form id="reset-form"><label for="password">New password</label><input id="password" type="password" minlength="8" autocomplete="new-password" required><label for="confirmation">Confirm new password</label><input id="confirmation" type="password" minlength="8" autocomplete="new-password" required><button id="submit" type="submit">Reset password</button></form><p class="message" id="message" role="alert" aria-live="polite"></p></div><div class="success" id="success-state"><div class="success-mark">✓</div><div class="eyebrow">Password updated</div><h1>You are all set.</h1><p>Your SportMate password has been reset successfully.</p><div class="back-note">Return to the SportMate app and sign in.</div></div></section></main><script>
const endpoint=__ENDPOINT__;const form=document.getElementById('reset-form');const message=document.getElementById('message');const submit=document.getElementById('submit');
form.addEventListener('submit',async event=>{event.preventDefault();const password=document.getElementById('password').value;const confirmation=document.getElementById('confirmation').value;if(password!==confirmation){message.textContent='Passwords do not match.';return}submit.disabled=true;submit.textContent='Resetting…';message.textContent='';try{const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password})});const data=await response.json().catch(()=>({}));if(!response.ok){message.textContent=data?.error?.message||data?.error||'Password reset could not be completed.';submit.disabled=false;submit.textContent='Reset password';return}document.getElementById('form-state').style.display='none';document.getElementById('success-state').style.display='block'}catch(_){message.textContent='Connection problem. Please try again.';submit.disabled=false;submit.textContent='Reset password'}});
</script></body></html>'''
    return HttpResponse(html.replace('__ENDPOINT__', endpoint_json))


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
    # The client login form accepts either an email address or a username.
    # Django's default authentication backend uses the USERNAME_FIELD, so pass
    # the submitted identifier through as `username` regardless of its form.
    identifier = (request.data.get('identifier') or request.data.get('email') or '').strip()
    password = request.data.get('password')

    user = AuthenticationService.login(identifier=identifier, password=password)

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
    email = (request.data.get('email') or '').strip().lower()

    try:
        user = User.objects.get(email__iexact=(email or '').strip())
    except User.DoesNotExist:
        # Do not reveal which email addresses have accounts.
        return Response({"msg": "If that email is registered, a reset link has been sent."})

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = PasswordResetTokenGenerator().make_token(user)

    separator = '&' if '?' in settings.PASSWORD_RESET_URL else '?'
    reset_link = f"{settings.PASSWORD_RESET_URL}{separator}{urlencode({'uid': uid, 'token': token})}"

    send_mail(
        subject="Reset Password - SportMate",
        message=f"Click to reset password: {reset_link}",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
    )

    return Response({"msg": "If that email is registered, a reset link has been sent."})


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request, uidb64=None, token=None):
    new_password = request.data.get('password')
    if not new_password or len(new_password) < 8:
        return Response({"error": "Password must be at least 8 characters."}, status=400)

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
    serializer_class = UserSerializer
    permission_classes = [IsActiveAccount]

    def get_object(self):
        return self.request.user

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(self.get_object(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = UserService.update_profile(user=request.user, validated_data=serializer.validated_data)
        return Response(CurrentUserSerializer(user, context={'request': request}).data)


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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'booking'
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'booking'

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


# ---------------- OSM NEARBY GROUNDS ----------------
class UniversalGroundsView(APIView):
    """Return cached OSM sports features, refreshing a sparse local area once."""
    permission_classes = [AllowAny]
    radius_km = 20.0
    cache_minimum = 3
    refresh_cooldown_seconds = 900

    def get(self, request):
        try:
            latitude = float(request.query_params.get('latitude'))
            longitude = float(request.query_params.get('longitude'))
        except (TypeError, ValueError):
            return Response({'detail': 'Valid latitude and longitude query parameters are required.'}, status=400)
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return Response({'detail': 'Latitude must be between -90 and 90 and longitude between -180 and 180.'}, status=400)
        grounds = self._nearby_cached(latitude, longitude)
        # A sparse cache is normal in a new area. Only one request per nearby
        # area may wait on Overpass during the cooldown; subsequent callers
        # receive locally cached results immediately instead of queueing behind
        # the same public API request.
        refresh_key = self._refresh_cache_key(latitude, longitude)
        should_refresh = len(grounds) < self.cache_minimum and cache.add(
            refresh_key, True, timeout=self.refresh_cooldown_seconds,
        )
        if should_refresh:
            try:
                self._refresh_from_overpass(latitude, longitude)
            except requests.RequestException:
                pass  # Return the cache if the public provider is unavailable.
            grounds = self._nearby_cached(latitude, longitude)
        payload = [{'id': ground.id, 'name': ground.name, 'sport': ground.sport,
                    'ground_type': ground.ground_type, 'latitude': ground.latitude,
                    'longitude': ground.longitude, 'distance_km': round(distance, 2)}
                   for ground, distance in grounds]
        return Response({'count': len(payload), 'radius_km': self.radius_km, 'grounds': payload})

    @staticmethod
    def _refresh_cache_key(latitude, longitude):
        # 0.2 degree cells are smaller than the search diameter while still
        # absorbing normal GPS movement around the same user area.
        return f'osm-ground-refresh:{latitude:.1f}:{longitude:.1f}'

    def _nearby_cached(self, latitude, longitude):
        lat_delta = self.radius_km / 111.0
        lon_delta = self.radius_km / max(111.0 * math.cos(math.radians(latitude)), 0.000001)
        candidates = SportsGround.objects.filter(
            latitude__range=(latitude - lat_delta, latitude + lat_delta),
            longitude__range=(longitude - lon_delta, longitude + lon_delta),
        )
        grounds = [(ground, haversine_distance(latitude, longitude, ground.latitude, ground.longitude))
                   for ground in candidates]
        return sorted((item for item in grounds if item[1] <= self.radius_km), key=lambda item: item[1])

    def _refresh_from_overpass(self, latitude, longitude):
        query = ('[out:json][timeout:30];('
                 f'node["leisure"~"^(pitch|sports_centre|stadium)$"](around:20000,{latitude},{longitude});'
                 f'way["leisure"~"^(pitch|sports_centre|stadium)$"](around:20000,{latitude},{longitude});'
                 f'rel["leisure"~"^(pitch|sports_centre|stadium)$"](around:20000,{latitude},{longitude});'
                 ');out center tags;')
        response = requests.post(
            settings.OVERPASS_API_URL,
            data={'data': query},
            timeout=(3, settings.MAPS_PROVIDER_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        elements = response.json().get('elements', [])
        if not isinstance(elements, list):
            return
        for element in elements:
            if not isinstance(element, dict):
                continue
            center = element.get('center') or {}
            try:
                element_latitude = float(element.get('lat', center.get('lat')))
                element_longitude = float(element.get('lon', center.get('lon')))
            except (TypeError, ValueError):
                continue
            if haversine_distance(latitude, longitude, element_latitude, element_longitude) > self.radius_km:
                continue
            tags = element.get('tags') or {}
            if not isinstance(tags, dict):
                continue
            SportsGround.objects.update_or_create(
                osm_id=f"{element.get('type', 'feature')}/{element.get('id')}",
                defaults={'name': clean_google_maps_name(str(tags.get('name') or tags.get('operator') or 'Unnamed Sports Ground')),
                          'ground_type': str(tags.get('leisure') or 'sports_ground'),
                          'sport': str(tags.get('sport') or tags.get('sport:1') or 'multi-sport'),
                          'latitude': element_latitude, 'longitude': element_longitude},
            )


# ---------------- GROUNDS ----------------
class GroundListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            # Accept the established short names and the longer names used by
            # older app builds, so a client update cannot make grounds vanish.
            lat_value = request.GET.get('lat') or request.GET['latitude']
            lng_value = request.GET.get('lng') or request.GET['longitude']
            lat = float(lat_value)
            lng = float(lng_value)
        except (KeyError, TypeError, ValueError):
            return Response({'detail': 'Valid lat and lng query parameters are required.'}, status=400)

        grounds = Ground.objects.prefetch_related('sports').all()
        sport = request.GET.get('sport', '').strip()
        size = request.GET.get('size', '').strip().lower()
        try:
            radius = float(request.GET.get('radius', 10))
        except ValueError:
            return Response({'detail': 'radius must be a number.'}, status=400)
        if radius <= 0 or radius > 100:
            return Response({'detail': 'radius must be between 0 and 100 km.'}, status=400)
        if sport:
            grounds = grounds.filter(sports__name__iexact=sport)
        city = request.GET.get('city', '').strip()
        if city:
            grounds = grounds.filter(city__iexact=city)
        if size:
            if size not in dict(Ground.SIZE_CHOICES):
                return Response({'detail': 'size must be small, medium, or big.'}, status=400)
            grounds = grounds.filter(size=size)
        # Calculate and filter distance in SQL.  The previous Python loop read
        # every Ground row before filtering, which degrades sharply as the
        # catalogue grows.
        grounds = nearby_queryset(grounds, latitude=lat, longitude=lng, radius=radius)
        serialized = GroundSerializer(grounds, many=True).data
        # Match the app's standard collection envelope; the Flutter client
        # accepts both this and the legacy bare-list response.
        return Response({'count': len(serialized), 'results': serialized})


class LiveGroundListView(APIView):
    """Returns current Google Maps results; it never reads Ground rows."""
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            latitude = float(request.GET['lat'])
            longitude = float(request.GET['lng'])
            radius = float(request.GET.get('radius', 10))
        except (KeyError, TypeError, ValueError):
            return Response({'detail': 'Valid lat and lng query parameters are required.'}, status=400)
        if not 0 < radius <= 50:
            return Response({'detail': 'radius must be between 0 and 50 km.'}, status=400)
        try:
            places = find_live_sports_grounds(
                latitude=latitude,
                longitude=longitude,
                sport=request.GET.get('sport', '').strip() or None,
                radius_km=radius,
            )
        except MapsProviderError as error:
            return Response({'detail': str(error)}, status=503)
        return Response(places)


class GroundDiscoveryView(APIView):
    """Discover provider places and upsert them into the Ground catalogue."""
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'search'

    def post(self, request):
        serializer = GroundDiscoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            places = find_live_sports_grounds(
                latitude=data['latitude'], longitude=data['longitude'],
                sport=data.get('sport'), radius_km=data['radius'],
            )
        except MapsProviderError as error:
            return Response({'success': False, 'error': {'code': 'GROUND_PROVIDER_UNAVAILABLE', 'message': str(error)}}, status=503)
        grounds, created_count = GroundIngestionService.ingest(
            places=places,
            requested_sport=data.get('sport'),
            fallback_city=request.user.city,
        )
        # This is a user-owned update; exact coordinates are never exposed by
        # public player serializers.
        request.user.latitude = data['latitude']
        request.user.longitude = data['longitude']
        request.user.save(update_fields=['latitude', 'longitude', 'updated_at'])
        return Response({
            'success': True,
            'created_count': created_count,
            'data': GroundSerializer(grounds, many=True).data,
        })


# ---------------- MATCHES ----------------
class MatchListView(generics.ListAPIView):
    queryset = Match.objects.select_related('sport', 'ground', 'organizer').prefetch_related('joined_players')
    serializer_class = MatchSerializer
    permission_classes = [IsActiveAccount]


class CreateMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        required = ('sport', 'ground', 'date_time', 'total_players')
        missing = [field for field in required if not request.data.get(field)]
        if missing:
            return Response({'detail': f"Missing required fields: {', '.join(missing)}."}, status=400)
        try:
            match = Match.objects.create(
                sport_id=request.data['sport'], organizer=request.user,
                ground_id=request.data['ground'], date_time=request.data['date_time'],
                total_players=int(request.data['total_players'])
            )
        except (Sport.DoesNotExist, Ground.DoesNotExist):
            return Response({'detail': 'Selected sport or ground does not exist.'}, status=400)
        except (TypeError, ValueError):
            return Response({'detail': 'Player capacity must be a valid number.'}, status=400)
        return Response({'match': MatchSerializer(match).data}, status=201)


class JoinMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, match_id):
        match, joined = join_match(match_id=match_id, user=request.user)
        return Response({
            'success': True,
            'message': 'Joined successfully' if joined else 'Already joined this match.',
            'match': MatchSerializer(match).data,
        })


# ---------------- COURT-BACKED GAMES ----------------
class GameListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsActiveAccount]

    def get_serializer_class(self):
        return GameCreateSerializer if self.request.method == 'POST' else GameSerializer

    def get_queryset(self):
        query = GameQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        visible_to_requester = (
            Q(visibility=Game.Visibility.PUBLIC)
            | Q(host=self.request.user)
            | Q(game_players__user=self.request.user, game_players__status=GamePlayer.Status.CONFIRMED)
        )
        queryset = Game.objects.filter(visible_to_requester).select_related(
            'sport', 'venue', 'court__sport', 'host',
        ).prefetch_related('game_players__user__user_sports__sport').distinct()
        if data.get('sport'):
            queryset = queryset.filter(sport_id=data['sport'])
        if data.get('venue'):
            queryset = queryset.filter(venue__public_id=data['venue'])
        if data.get('city'):
            queryset = queryset.filter(venue__city__iexact=data['city'])
        if data.get('date'):
            queryset = queryset.filter(game_date=data['date'])
        if data.get('skill_level'):
            queryset = queryset.filter(skill_level=data['skill_level'])
        if data.get('status'):
            queryset = queryset.filter(status=data['status'])
        elif data.get('upcoming', True):
            queryset = queryset.filter(status__in=ACTIVE_GAME_STATUSES, ends_at__gt=timezone.now())
        if 'latitude' in data:
            queryset = nearby_queryset(
                queryset, latitude=data['latitude'], longitude=data['longitude'], radius=data['radius'],
                latitude_field='venue__latitude', longitude_field='venue__longitude',
            )
        else:
            queryset = queryset.order_by('starts_at')
        return queryset

    def list(self, request, *args, **kwargs):
        # Refresh the lifecycle for records that will be presented. This keeps
        # time/capacity state authoritative without a client-side transition.
        queryset = self.filter_queryset(self.get_queryset())
        for game in queryset[:100]:
            GameLifecycleService.refresh(game)
        self.get_queryset = lambda: queryset
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = GameCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        game = GameService.create(
            host=request.user, sport_id=data['sport'].id, venue_id=data['venue_id'], court_id=data['court_id'],
            game_date=data['game_date'], start_time=data['start_time'], end_time=data['end_time'],
            min_players=data['min_players'], max_players=data['max_players'], skill_level=data['skill_level'],
            description=data['description'], visibility=data['visibility'],
        )
        game = Game.objects.select_related('sport', 'venue', 'court__sport', 'host').prefetch_related(
            'game_players__user__user_sports__sport',
        ).get(pk=game.pk)
        return Response({'success': True, 'data': GameSerializer(game, context={'request': request}).data}, status=201)


class GameDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsActiveAccount]
    serializer_class = GameSerializer
    lookup_field = 'public_id'

    def get_queryset(self):
        visible_to_requester = (
            Q(visibility=Game.Visibility.PUBLIC)
            | Q(host=self.request.user)
            | Q(game_players__user=self.request.user, game_players__status=GamePlayer.Status.CONFIRMED)
        )
        return Game.objects.filter(visible_to_requester).select_related(
            'sport', 'venue', 'court__sport', 'host',
        ).prefetch_related('game_players__user__user_sports__sport').distinct()

    def retrieve(self, request, *args, **kwargs):
        game = self.get_object()
        GameLifecycleService.refresh(game)
        return Response({'success': True, 'data': GameSerializer(game, context={'request': request}).data})

    def partial_update(self, request, *args, **kwargs):
        serializer = GameUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        game = GameService.update(game_id=kwargs['public_id'], actor=request.user, **serializer.validated_data)
        game = self.get_queryset().get(pk=game.pk)
        return Response({'success': True, 'data': GameSerializer(game, context={'request': request}).data})

    def destroy(self, request, *args, **kwargs):
        GameService.cancel(game_id=kwargs['public_id'], actor=request.user)
        return Response(status=204)


class GameJoinView(APIView):
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'game_join'

    def post(self, request, public_id):
        game, joined = GameService.join(game_id=public_id, user=request.user)
        game = Game.objects.select_related('sport', 'venue', 'court__sport', 'host').prefetch_related(
            'game_players__user__user_sports__sport',
        ).get(pk=game.pk)
        return Response({
            'success': True,
            'message': 'Joined game.' if joined else 'You have already joined this game.',
            'data': GameSerializer(game, context={'request': request}).data,
        })


class GameLeaveView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        game, action = GameService.leave(game_id=public_id, user=request.user)
        return Response({'success': True, 'message': 'Game cancelled by host.' if action == 'cancelled' else 'Left game.', 'status': game.status})


class GamePlayersView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        visible_to_requester = (
            Q(visibility=Game.Visibility.PUBLIC)
            | Q(host=request.user)
            | Q(game_players__user=request.user, game_players__status=GamePlayer.Status.CONFIRMED)
        )
        game = get_object_or_404(Game.objects.filter(visible_to_requester).distinct(), public_id=public_id)
        players = GamePlayer.objects.filter(game=game, status=GamePlayer.Status.CONFIRMED).select_related('user').prefetch_related('user__user_sports__sport')
        return Response({'success': True, 'results': GamePlayerSerializer(players, many=True, context={'request': request}).data})


class GameRecommendationView(APIView):
    """Deterministic recommendations; all scoring lives in GameMatchingService."""
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'recommendations'

    def get(self, request):
        query = GameRecommendationQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        latitude = data.get('latitude', request.user.latitude)
        longitude = data.get('longitude', request.user.longitude)
        # A saved location is optional. Without it the service still returns
        # compatible upcoming games, without distance scoring/filtering.
        if latitude is None or longitude is None:
            latitude = longitude = None
        results = GameMatchingService.get_recommendations(
            user=request.user, sport=data.get('sport'), latitude=latitude,
            longitude=longitude, radius=data['radius'], date=data.get('date'),
            skill_level=data.get('skill_level'), limit=data['limit'],
        )
        return Response({
            'success': True,
            'data': [
                {
                    'game': GameSerializer(item['game'], context={'request': request}).data,
                    'match_score': item['match_score'],
                    'distance_km': item['distance_km'],
                    'reasons': item['reasons'],
                }
                for item in results
            ],
        })


class RecommendationBaseView(APIView):
    """Read-only deterministic recommendations; category scoring stays in services."""
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'recommendations'

    @staticmethod
    def _limit(request):
        try:
            return min(50, max(1, int(request.query_params.get('limit', 10))))
        except ValueError as exc:
            raise ValidationError({'limit': ['Must be an integer between 1 and 50.']}) from exc

    @staticmethod
    def _venue(item):
        venue = item['venue']
        return {'type': 'venue', 'id': str(venue.public_id), 'title': venue.name,
                'description': venue.description, 'score': item['score'],
                'distance_km': item.get('distance_km'), 'reasons': item['reasons'],
                'metadata': {'city': venue.city, 'rating': str(venue.rating)}}

    @staticmethod
    def _community(item):
        community = item['community']
        return {'type': 'community', 'id': str(community.public_id), 'title': community.name,
                'description': community.description, 'score': item['score'],
                'reasons': item['reasons'], 'metadata': {'sport': community.sport.name if community.sport_id else None}}

    @staticmethod
    def _event(item):
        event = item['event']
        return {'type': 'event', 'id': str(event.public_id), 'title': event.title,
                'description': event.description, 'score': item['score'], 'reasons': item['reasons'],
                'metadata': {'sport': event.sport.name, 'starts_at': event.starts_at.isoformat(), 'venue': event.venue.name}}

    @staticmethod
    def _tournament(item):
        tournament = item['tournament']
        return {'type': 'tournament', 'id': str(tournament.public_id), 'title': tournament.name,
                'description': '', 'score': item['score'], 'reasons': item['reasons'],
                'metadata': {'sport': tournament.sport.name, 'start_date': tournament.start_date.isoformat(), 'venue': tournament.venue.name}}


class RecommendationOverviewView(RecommendationBaseView):
    def get(self, request):
        limit = self._limit(request)
        profile = RecommendationProfileService.profile(request.user)
        return Response({'success': True, 'algorithm': 'deterministic_hybrid_v1', 'data': {
            'profile': profile.preferences,
            'venues': [self._venue(item) for item in RecommendationService.venues(request.user, limit)],
            'communities': [self._community(item) for item in RecommendationService.communities(request.user, limit)],
            'events': [self._event(item) for item in RecommendationService.events(request.user, limit)],
            'tournaments': [self._tournament(item) for item in RecommendationService.tournaments(request.user, limit)],
        }})


class VenueRecommendationView(RecommendationBaseView):
    def get(self, request):
        RecommendationProfileService.profile(request.user)
        return Response({'success': True, 'data': [self._venue(item) for item in RecommendationService.venues(request.user, self._limit(request))]})


class CommunityRecommendationView(RecommendationBaseView):
    def get(self, request):
        RecommendationProfileService.profile(request.user)
        return Response({'success': True, 'data': [self._community(item) for item in RecommendationService.communities(request.user, self._limit(request))]})


class EventRecommendationView(RecommendationBaseView):
    def get(self, request):
        RecommendationProfileService.profile(request.user)
        return Response({'success': True, 'data': [self._event(item) for item in RecommendationService.events(request.user, self._limit(request))]})


class TournamentRecommendationView(RecommendationBaseView):
    def get(self, request):
        RecommendationProfileService.profile(request.user)
        return Response({'success': True, 'data': [self._tournament(item) for item in RecommendationService.tournaments(request.user, self._limit(request))]})

# ---------------- EVENTS & TOURNAMENTS ----------------
class EventListCreateView(generics.ListCreateAPIView):
 permission_classes=[IsActiveAccount]; serializer_class=EventSerializer
 def get_queryset(self): return Event.objects.filter(status__in=[Event.Status.OPEN,Event.Status.FULL]).select_related('sport','venue','court','community').order_by('starts_at')
 def create(self,request,*args,**kwargs):
  s=EventCreateSerializer(data=request.data);s.is_valid(raise_exception=True);d=s.validated_data
  d['venue']=get_object_or_404(Venue,public_id=d.pop('venue_id')); d['court']=get_object_or_404(Court,public_id=d.pop('court_id')) if d.get('court_id') else None; d.pop('court_id',None)
  d['community']=get_object_or_404(Community,public_id=d.pop('community_id')) if d.get('community_id') else None; d.pop('community_id',None); d['game']=get_object_or_404(Game,public_id=d.pop('game_id')) if d.get('game_id') else None; d.pop('game_id',None)
  event=EventService.create(organizer=request.user,**d); return Response({'success':True,'data':EventSerializer(event).data},status=201)
class EventDetailView(generics.RetrieveUpdateDestroyAPIView):
 permission_classes=[IsActiveAccount];serializer_class=EventSerializer;lookup_field='public_id'
 def get_queryset(self): return Event.objects.select_related('sport','venue','court','community')
 def destroy(self,request,*args,**kwargs):
  e=self.get_object()
  if e.organizer_id!=request.user.id and not request.user.is_staff: raise PermissionDenied('Only organizer can cancel.')
  e.status=Event.Status.CANCELLED;e.save(update_fields=['status','updated_at']);e.participants.filter(status__in=['registered','waitlisted']).update(status=EventParticipant.Status.CANCELLED,left_at=timezone.now());return Response(status=204)
class EventRegistrationView(APIView):
 permission_classes=[IsActiveAccount]
 throttle_classes=[ScopedRateThrottle]
 throttle_scope='event_registration'
 def post(self,request,public_id):
  p,_=EventRegistrationService.register(event=get_object_or_404(Event,public_id=public_id),user=request.user);return Response({'success':True,'data':EventParticipantSerializer(p).data})
class EventLeaveView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id): EventRegistrationService.leave(event=get_object_or_404(Event,public_id=public_id),user=request.user);return Response({'success':True})
class EventCancelView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id):
  e=get_object_or_404(Event,public_id=public_id)
  if e.organizer_id!=request.user.id and not request.user.is_staff: raise PermissionDenied('Only organizer can cancel.')
  e.status=Event.Status.CANCELLED;e.save(update_fields=['status','updated_at']);e.participants.filter(status__in=['registered','waitlisted']).update(status=EventParticipant.Status.CANCELLED,left_at=timezone.now());return Response({'success':True,'data':EventSerializer(e).data})
class EventParticipantsView(generics.ListAPIView):
 permission_classes=[IsActiveAccount];serializer_class=EventParticipantSerializer
 def get_queryset(self): return EventParticipant.objects.filter(event__public_id=self.kwargs['public_id']).select_related('user').order_by('joined_at')
class EventParticipantRemoveView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id,user_id):
  e=get_object_or_404(Event,public_id=public_id)
  if e.organizer_id!=request.user.id and not request.user.is_staff: raise PermissionDenied('Only organizer can remove participants.')
  return Response({'success':True,'data':EventParticipantSerializer(EventRegistrationService.leave(event=e,user=get_object_or_404(User,pk=user_id),removed=True)).data})
class TournamentListCreateView(generics.ListCreateAPIView):
 permission_classes=[IsActiveAccount];serializer_class=TournamentSerializer
 def get_queryset(self): return Tournament.objects.select_related('sport','venue').order_by('start_date')
 def create(self,request,*args,**kwargs):
  s=TournamentCreateSerializer(data=request.data);s.is_valid(raise_exception=True);d=s.validated_data;d['venue']=get_object_or_404(Venue,public_id=d.pop('venue_id'))
  t=Tournament.objects.create(tournament_reference=EventService.ref('TR'),organizer=request.user,**d);return Response({'success':True,'data':TournamentSerializer(t).data},status=201)
class TournamentDetailView(generics.RetrieveUpdateAPIView):
 permission_classes=[IsActiveAccount];serializer_class=TournamentSerializer;lookup_field='public_id';queryset=Tournament.objects.select_related('sport','venue')
class TournamentCancelView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id):
  t=get_object_or_404(Tournament,public_id=public_id)
  if t.organizer_id!=request.user.id and not request.user.is_staff: raise PermissionDenied('Only organizer can cancel.')
  if t.status==Tournament.Status.COMPLETED: raise ValidationError('Completed tournaments cannot be cancelled.')
  t.status=Tournament.Status.CANCELLED;t.save(update_fields=['status','updated_at']);return Response({'success':True,'data':TournamentSerializer(t).data})
class TournamentTeamsView(generics.ListCreateAPIView):
 permission_classes=[IsActiveAccount];serializer_class=TournamentTeamSerializer
 def get_queryset(self): return TournamentTeam.objects.filter(tournament__public_id=self.kwargs['public_id']).select_related('captain')
 def create(self,request,*args,**kwargs):
  t=get_object_or_404(Tournament,public_id=self.kwargs['public_id']);name=(request.data.get('name') or '').strip()
  if not name: raise ValidationError({'name':['Required.']})
  return Response({'success':True,'data':TournamentTeamSerializer(TournamentService.create_team(tournament=t,creator=request.user,name=name)).data},status=201)
class TournamentMatchesView(generics.ListAPIView):
 permission_classes=[IsActiveAccount];serializer_class=TournamentMatchSerializer
 def get_queryset(self): return TournamentMatch.objects.filter(tournament__public_id=self.kwargs['public_id']).order_by('round_number','match_number')
class TournamentBracketView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id):
  matches=TournamentBracketService.generate(tournament=get_object_or_404(Tournament,public_id=public_id),actor=request.user);return Response({'success':True,'data':TournamentMatchSerializer(matches,many=True).data})
class TournamentResultView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id,match_id):
  m=get_object_or_404(TournamentMatch,tournament__public_id=public_id,public_id=match_id);m=TournamentMatchService.result(match=m,actor=request.user,score_a=request.data.get('score_a'),score_b=request.data.get('score_b'));return Response({'success':True,'data':TournamentMatchSerializer(m).data})
class TournamentStandingsView(APIView):
 permission_classes=[IsActiveAccount]
 def get(self,request,public_id):
  return Response({'success':True,'data':[{'team':r['team'].name,'played':r['played'],'wins':r['wins'],'losses':r['losses'],'draws':r['draws'],'points':r['points'],'difference':r['difference']} for r in TournamentStandingsService.standings(get_object_or_404(Tournament,public_id=public_id))]})


# ---------------- COMMUNITIES ----------------
class CommunityListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsActiveAccount]

    def get_serializer_class(self):
        return CommunityCreateSerializer if self.request.method == 'POST' else CommunitySerializer

    def get_queryset(self):
        visible = Q(visibility=Community.Visibility.PUBLIC) | Q(owner=self.request.user) | Q(members__user=self.request.user, members__status=CommunityMember.Status.ACTIVE)
        queryset = Community.objects.filter(visible, is_active=True).select_related('sport', 'owner').distinct()
        sport = self.request.query_params.get('sport')
        city = self.request.query_params.get('city')
        if sport:
            queryset = queryset.filter(sport_id=sport)
        if city:
            queryset = queryset.filter(city__iexact=city)
        latitude = self.request.query_params.get('latitude', self.request.user.latitude)
        longitude = self.request.query_params.get('longitude', self.request.user.longitude)
        radius = self.request.query_params.get('radius', 20)
        if latitude is not None and longitude is not None:
            ranked = CommunityDiscoveryService.discover(user=self.request.user, queryset=queryset, latitude=latitude, longitude=longitude, radius=radius)
            ids = [item.id for item in ranked]
            # Preserve deterministic service order through a lightweight list.
            return [next(item for item in ranked if item.id == pk) for pk in ids]
        return CommunityDiscoveryService.discover(user=self.request.user, queryset=queryset)

    def create(self, request, *args, **kwargs):
        serializer = CommunityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        community = CommunityService.create(owner=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': CommunitySerializer(community).data}, status=201)


class CommunityDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get_community(self, public_id):
        community = get_object_or_404(Community, public_id=public_id, is_active=True)
        if community.visibility == Community.Visibility.PRIVATE:
            CommunityMembershipService.active_member(community=community, user=self.request.user)
        return community

    def get(self, request, public_id):
        return Response({'success': True, 'data': CommunitySerializer(self.get_community(public_id)).data})

    def patch(self, request, public_id):
        community = self.get_community(public_id)
        CommunityMembershipService.admin(community=community, user=request.user)
        serializer = CommunityUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.validated_data.items():
            setattr(community, key, value)
        community.save(update_fields=[*serializer.validated_data.keys(), 'updated_at'])
        return Response({'success': True, 'data': CommunitySerializer(community).data})


class CommunityJoinLeaveView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id, action):
        community = get_object_or_404(Community, public_id=public_id)
        if action == 'join':
            member, changed = CommunityService.join(community=community, user=request.user)
            return Response({'success': True, 'joined': member.status == CommunityMember.Status.ACTIVE, 'pending': member.status == CommunityMember.Status.PENDING, 'changed': changed})
        CommunityService.leave(community=community, user=request.user)
        return Response({'success': True, 'message': 'Left community.'})


class CommunityMembersView(generics.ListAPIView):
    serializer_class = CommunityMemberSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        community = get_object_or_404(Community, public_id=self.kwargs['public_id'], is_active=True)
        if community.visibility == Community.Visibility.PRIVATE:
            CommunityMembershipService.active_member(community=community, user=self.request.user)
        return CommunityMember.objects.filter(community=community, status=CommunityMember.Status.ACTIVE).select_related('user').order_by('joined_at')


class CommunityRequestsView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        community = get_object_or_404(Community, public_id=public_id)
        CommunityMembershipService.admin(community=community, user=request.user)
        members = CommunityMember.objects.filter(community=community, status=CommunityMember.Status.PENDING).select_related('user')
        return Response({'success': True, 'data': CommunityMemberSerializer(members, many=True).data})

    def post(self, request, public_id, user_id, action):
        community = get_object_or_404(Community, public_id=public_id)
        member = CommunityService.decide_request(community=community, actor=request.user, user_id=user_id, approve=(action == 'approve'))
        return Response({'success': True, 'data': CommunityMemberSerializer(member).data})


class CommunityPostsView(generics.ListCreateAPIView):
    serializer_class = CommunityPostSerializer
    permission_classes = [IsActiveAccount]

    def get_community(self):
        community = get_object_or_404(Community, public_id=self.kwargs['public_id'], is_active=True)
        if community.visibility == Community.Visibility.PRIVATE:
            CommunityMembershipService.active_member(community=community, user=self.request.user)
        return community

    def get_queryset(self):
        return CommunityPost.objects.filter(community=self.get_community()).select_related('author', 'game').annotate(like_count=Count('likes'), comment_count=Count('comments')).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = CommunityPostCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = CommunityPostService.create(community=self.get_community(), author=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': CommunityPostSerializer(post).data}, status=201)


class CommunityPostDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get_post(self, public_id):
        post = get_object_or_404(CommunityPost.objects.select_related('community', 'author'), public_id=public_id)
        if post.community.visibility == Community.Visibility.PRIVATE:
            CommunityMembershipService.active_member(community=post.community, user=self.request.user)
        return post

    def get(self, request, public_id):
        return Response({'success': True, 'data': CommunityPostSerializer(self.get_post(public_id)).data})

    def patch(self, request, public_id):
        serializer = CommunityContentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        post = CommunityPostService.edit(post=self.get_post(public_id), actor=request.user, content=serializer.validated_data['content'])
        return Response({'success': True, 'data': CommunityPostSerializer(post).data})

    def delete(self, request, public_id):
        post = CommunityPostService.delete(post=self.get_post(public_id), actor=request.user)
        return Response({'success': True, 'data': CommunityPostSerializer(post).data})


class CommunityCommentsView(generics.ListCreateAPIView):
    serializer_class = CommunityCommentSerializer
    permission_classes = [IsActiveAccount]

    def get_post(self):
        return get_object_or_404(CommunityPost.objects.select_related('community'), public_id=self.kwargs['public_id'])

    def get_queryset(self):
        post = self.get_post()
        if post.community.visibility == Community.Visibility.PRIVATE:
            CommunityMembershipService.active_member(community=post.community, user=self.request.user)
        return CommunityComment.objects.filter(post=post).select_related('author').order_by('created_at')

    def create(self, request, *args, **kwargs):
        serializer = CommunityContentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = CommunityCommentService.create(post=self.get_post(), author=request.user, content=serializer.validated_data['content'])
        return Response({'success': True, 'data': CommunityCommentSerializer(comment).data}, status=201)


class CommunityCommentDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request, public_id):
        serializer = CommunityContentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = get_object_or_404(CommunityComment.objects.select_related('post__community'), public_id=public_id)
        return Response({'success': True, 'data': CommunityCommentSerializer(CommunityCommentService.change(comment=comment, actor=request.user, content=serializer.validated_data['content'])).data})

    def delete(self, request, public_id):
        comment = get_object_or_404(CommunityComment.objects.select_related('post__community'), public_id=public_id)
        return Response({'success': True, 'data': CommunityCommentSerializer(CommunityCommentService.change(comment=comment, actor=request.user, delete=True)).data})


class CommunityPostLikeView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        post = get_object_or_404(CommunityPost.objects.select_related('community'), public_id=public_id, deleted_at__isnull=True)
        CommunityMembershipService.active_member(community=post.community, user=request.user)
        _, created = CommunityPostLike.objects.get_or_create(post=post, user=request.user)
        return Response({'success': True, 'liked': True, 'created': created, 'like_count': post.likes.count()}, status=201 if created else 200)

    def delete(self, request, public_id):
        post = get_object_or_404(CommunityPost.objects.select_related('community'), public_id=public_id)
        CommunityMembershipService.active_member(community=post.community, user=request.user)
        CommunityPostLike.objects.filter(post=post, user=request.user).delete()
        return Response({'success': True, 'liked': False, 'like_count': post.likes.count()})


class CommunityModerationView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id, user_id, action):
        community = get_object_or_404(Community, public_id=public_id)
        member = CommunityModerationService.member_action(community=community, actor=request.user, user_id=user_id, ban=(action == 'ban'))
        return Response({'success': True, 'data': CommunityMemberSerializer(member).data})


# ---------------- CONVERSATION CHAT ----------------
class ConversationListCreateView(generics.GenericAPIView):
    permission_classes = [IsActiveAccount]

    def _summary(self, conversation, latest_messages):
        member = next((item for item in conversation.members.all() if item.user_id == self.request.user.id), None)
        other = None
        if conversation.conversation_type == Conversation.Type.ONE_TO_ONE:
            other = conversation.participant_two if conversation.participant_one_id == self.request.user.id else conversation.participant_one
        latest = latest_messages.get(conversation.id)
        return {
            'public_id': str(conversation.public_id),
            'type': conversation.conversation_type,
            'active': conversation.active,
            'other_user': ({'public_id': str(other.public_id), 'username': other.username, 'name': other.full_name} if other else None),
            'game': ({'public_id': str(conversation.game.public_id), 'game_reference': conversation.game.game_reference} if conversation.game_id else None),
            'latest_message': ConversationMessageSerializer(latest).data if latest else None,
            'latest_message_at': latest.created_at if latest else conversation.updated_at,
            'unread_count': getattr(conversation, 'unread_count', 0),
        }

    def get_queryset(self):
        return Conversation.objects.filter(
            active=True, members__user=self.request.user, members__is_active=True,
        ).select_related('participant_one', 'participant_two', 'game').prefetch_related(
            'members',
        ).annotate(
            unread_count=Count(
                'messages',
                filter=(
                    (Q(messages__created_at__gt=F('members__last_read_at')) | Q(members__last_read_at__isnull=True))
                    & ~Q(messages__sender=F('members__user'))
                ),
                distinct=True,
            ),
        ).distinct().order_by('-updated_at')

    def get(self, request):
        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        conversations = page if page is not None else list(queryset)
        conversation_ids = [item.id for item in conversations]
        latest = {}
        for message in Message.objects.filter(conversation_id__in=conversation_ids).select_related('sender').order_by('conversation_id', '-created_at'):
            latest.setdefault(message.conversation_id, message)
        data = [self._summary(conversation, latest) for conversation in conversations]
        return self.get_paginated_response(data) if page is not None else Response({'success': True, 'data': data})

    def post(self, request):
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['type'] == Conversation.Type.ONE_TO_ONE:
            conversation, created = ConversationService.one_to_one(
                initiator=request.user,
                target_user_id=data.get('user_id'),
                target_user_public_id=data.get('user_public_id'),
            )
        else:
            conversation = ConversationService.game_for_user(game_id=data['game_id'], user=request.user)
            created = False
        conversation = self.get_queryset().get(pk=conversation.pk)
        return Response({'success': True, 'created': created, 'data': self._summary(conversation, {})}, status=201 if created else 200)


class ConversationDetailView(ConversationListCreateView):
    def get(self, request, public_id):
        conversation = get_object_or_404(self.get_queryset(), public_id=public_id)
        latest = Message.objects.filter(conversation=conversation).select_related('sender').order_by('-created_at').first()
        return Response({'success': True, 'data': self._summary(conversation, {conversation.id: latest} if latest else {})})


class ConversationMessagesView(generics.ListCreateAPIView):
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'messaging'
    serializer_class = ConversationMessageSerializer

    def get_conversation(self):
        conversation = get_object_or_404(Conversation.objects.select_related('participant_one', 'participant_two', 'game'), public_id=self.kwargs['public_id'])
        ChatPermissionService.active_member(conversation=conversation, user=self.request.user)
        return conversation

    def get_queryset(self):
        conversation = self.get_conversation()
        return Message.objects.filter(conversation=conversation).select_related('sender').order_by('created_at')

    def create(self, request, *args, **kwargs):
        serializer = ConversationMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = MessageService.send(
            conversation=self.get_conversation(), sender=request.user,
            content=serializer.validated_data['content'],
        )
        return Response({'success': True, 'data': ConversationMessageSerializer(message).data}, status=201)


class ConversationReadView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        conversation = get_object_or_404(Conversation.objects.filter(active=True), public_id=public_id)
        UnreadMessageService.mark_read(conversation=conversation, user=request.user)
        return Response({'success': True, 'message': 'Conversation marked as read.'})


class ConversationMessageDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get_message(self, public_id):
        message = get_object_or_404(Message.objects.select_related('conversation', 'sender'), public_id=public_id, conversation__isnull=False)
        if not self.request.user.is_staff:
            ChatPermissionService.active_member(conversation=message.conversation, user=self.request.user)
        return message

    def patch(self, request, public_id):
        serializer = ConversationMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = MessageService.edit(message=self.get_message(public_id), actor=request.user, content=serializer.validated_data['content'])
        return Response({'success': True, 'data': ConversationMessageSerializer(message).data})

    def delete(self, request, public_id):
        message = MessageService.delete(message=self.get_message(public_id), actor=request.user)
        return Response({'success': True, 'data': ConversationMessageSerializer(message).data})


# ---------------- LEGACY CHAT ----------------
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
        # Retain legacy direct-message unread state while also counting the
        # conversation-based chat API used by current mobile clients.
        legacy_count = Message.objects.filter(
            receiver=request.user,
            is_read=False
        ).count()
        members = list(ConversationMember.objects.filter(
            user=request.user,
            is_active=True,
            conversation__active=True,
        ).only('conversation_id', 'last_read_at'))
        last_read_by_conversation = {
            member.conversation_id: member.last_read_at for member in members
        }
        messages = Message.objects.filter(
            conversation_id__in=last_read_by_conversation,
            deleted_at__isnull=True,
        ).exclude(sender=request.user).only('conversation_id', 'created_at')
        conversation_count = sum(
            1 for message in messages
            if last_read_by_conversation[message.conversation_id] is None
            or message.created_at > last_read_by_conversation[message.conversation_id]
        )
        return Response({'unread': legacy_count + conversation_count})

# ---------------- NOTIFICATIONS ----------------
class GlobalSearchView(APIView):
    permission_classes=[IsActiveAccount]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='search'
    def get(self,request):
        q=(request.query_params.get('q') or '').strip()
        if not q: raise ValidationError({'q':['A search query is required.']})
        raw_types=request.query_params.get('type','').split(',') if request.query_params.get('type') else None
        if raw_types and not set(raw_types)<=VALID_TYPES: raise ValidationError({'type':['Unsupported search type.']})
        try: page=max(1,int(request.query_params.get('page',1)));page_size=min(100,max(1,int(request.query_params.get('page_size',20))))
        except ValueError as exc: raise ValidationError('page and page_size must be integers.') from exc
        latitude=request.query_params.get('latitude');longitude=request.query_params.get('longitude')
        if (latitude is None)!=(longitude is None): raise ValidationError('latitude and longitude must be provided together.')
        results=GlobalSearchService.search(user=request.user,q=q,types=raw_types,city=request.query_params.get('city'),latitude=latitude,longitude=longitude,radius=request.query_params.get('radius',20))
        start=(page-1)*page_size
        for result in results:result.pop('_score',None)
        return Response({'success':True,'count':len(results),'next':page+1 if start+page_size<len(results) else None,'previous':page-1 if page>1 else None,'results':results[start:start+page_size]})
class SearchSuggestionsView(APIView):
    permission_classes=[IsActiveAccount]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='search'
    def get(self,request):
        q=(request.query_params.get('q') or '').strip()
        if not q:return Response({'success':True,'data':[]})
        results=GlobalSearchService.search(user=request.user,q=q,types={'sport','player','venue','community','event'},latitude=None,longitude=None)[:10]
        for result in results:result.pop('_score',None)
        return Response({'success':True,'data':results})

class AchievementListView(generics.ListAPIView):
    permission_classes=[IsActiveAccount];serializer_class=AchievementSerializer;queryset=Achievement.objects.filter(is_active=True)
    lookup_field='public_id'
class AchievementDetailView(generics.RetrieveAPIView):
    permission_classes=[IsActiveAccount];serializer_class=AchievementSerializer;queryset=Achievement.objects.filter(is_active=True);lookup_field='public_id'
class UserAchievementsView(generics.ListAPIView):
    permission_classes=[IsActiveAccount];serializer_class=UserAchievementSerializer
    def get_queryset(self):
        user=request_user=self.request.user if self.kwargs.get('public_id') is None else get_object_or_404(User,public_id=self.kwargs['public_id'],is_active=True)
        AchievementEvaluationService.evaluate(user=user)
        return UserAchievement.objects.filter(user=user).select_related('achievement').order_by('achievement__category','achievement__name')
class MyRewardsView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request):
        AchievementEvaluationService.evaluate(user=request.user);return Response({'success':True,'data':{'badges':UserAchievementSerializer(UserAchievement.objects.filter(user=request.user,unlocked=True).select_related('achievement'),many=True).data}})
class MyLevelView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request):return Response({'success':True,'data':AchievementService.level(user=request.user)})
class MyProgressView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request):AchievementEvaluationService.evaluate(user=request.user);return Response({'success':True,'data':UserAchievementSerializer(UserAchievement.objects.filter(user=request.user).select_related('achievement'),many=True).data})


# ---------------- PLATFORM MODERATION ----------------
class ReportListCreateView(APIView):
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'reports'

    def get(self, request):
        queryset = Report.objects.select_related('reporter', 'assigned_moderator').order_by('-created_at')
        if not has_platform_role(request.user, MODERATOR_GROUPS):
            queryset = queryset.filter(reporter=request.user)
        else:
            if request.query_params.get('status'):
                queryset = queryset.filter(status=request.query_params['status'])
            if request.query_params.get('reason'):
                queryset = queryset.filter(reason=request.query_params['reason'])
            if request.query_params.get('target_type'):
                queryset = queryset.filter(target_type=request.query_params['target_type'])
            if request.query_params.get('moderator'):
                queryset = queryset.filter(assigned_moderator__public_id=request.query_params['moderator'])
            if request.query_params.get('created_after'):
                queryset = queryset.filter(created_at__date__gte=request.query_params['created_after'])
            if request.query_params.get('created_before'):
                queryset = queryset.filter(created_at__date__lte=request.query_params['created_before'])
        return Response({'success': True, 'data': ReportSerializer(queryset[:100], many=True).data})

    def post(self, request):
        serializer = ReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report, created = ModerationService.create_report(reporter=request.user, **serializer.validated_data)
        return Response({'success': True, 'created': created, 'data': ReportSerializer(report).data}, status=201 if created else 200)


class ReportDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get_object(self, request, public_id):
        queryset = Report.objects.select_related('reporter', 'assigned_moderator')
        if not has_platform_role(request.user, MODERATOR_GROUPS):
            queryset = queryset.filter(reporter=request.user)
        return get_object_or_404(queryset, public_id=public_id)

    def get(self, request, public_id):
        return Response({'success': True, 'data': ReportSerializer(self.get_object(request, public_id)).data})

    def patch(self, request, public_id):
        if not has_platform_role(request.user, MODERATOR_GROUPS):
            raise PermissionDenied('Platform moderator permission is required.')
        report = self.get_object(request, public_id)
        serializer = ReportUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        assigned = None
        if 'assigned_moderator_id' in data:
            assigned = get_object_or_404(User, public_id=data['assigned_moderator_id']) if data['assigned_moderator_id'] else None
            if assigned and not has_platform_role(assigned, MODERATOR_GROUPS):
                raise ValidationError({'assigned_moderator_id': ['Assignee must be a platform moderator.']})
        if data.get('action'):
            ModerationService.apply_action(
                moderator=request.user, report=report, target_type=report.target_type,
                target_public_id=report.target_public_id, action=data['action'],
                reason=data.get('action_reason', data.get('resolution', '')), ends_at=data.get('ends_at'),
            )
        report = ModerationService.update_report(
            report=report, moderator=request.user, status=data.get('status', report.status),
            resolution=data.get('resolution', report.resolution), assigned_moderator=assigned,
        )
        return Response({'success': True, 'data': ReportSerializer(report).data})


class AdminReportsView(ReportListCreateView):
    """Dedicated queue endpoint; unlike /reports/, never falls back to own reports."""
    permission_classes = [IsPlatformAdmin]


class AdminDashboardView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response({'success': True, 'data': {
            'active_users': User.objects.filter(is_active=True, account_status=User.AccountStatus.ACTIVE).count(),
            'new_users': User.objects.filter(date_joined__date=timezone.localdate()).count(),
            'games': Game.objects.count(), 'bookings': CourtBooking.objects.count(),
            'communities': Community.objects.filter(is_active=True).count(), 'events': Event.objects.count(),
            'reports_pending': Report.objects.filter(status=Report.Status.PENDING).count(),
            'reports_under_review': Report.objects.filter(status=Report.Status.UNDER_REVIEW).count(),
            'suspended_users': User.objects.filter(account_status__in=[User.AccountStatus.SUSPENDED, User.AccountStatus.DEACTIVATED]).count(),
        }})


class AdminUsersView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        queryset = User.objects.all().order_by('-date_joined')
        if request.query_params.get('status'):
            queryset = queryset.filter(account_status=request.query_params['status'])
        if request.query_params.get('q'):
            query = request.query_params['q']
            queryset = queryset.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(full_name__icontains=query))
        if request.query_params.get('joined_after'):
            queryset = queryset.filter(date_joined__date__gte=request.query_params['joined_after'])
        return Response({'success': True, 'data': AdminUserSerializer(queryset[:100], many=True).data})


class AdminModerationActionsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        queryset = ModerationAction.objects.select_related('moderator', 'report').order_by('-created_at')
        if request.query_params.get('target_type'):
            queryset = queryset.filter(target_type=request.query_params['target_type'])
        if request.query_params.get('moderator'):
            queryset = queryset.filter(moderator__public_id=request.query_params['moderator'])
        if request.query_params.get('action'):
            queryset = queryset.filter(action=request.query_params['action'])
        return Response({'success': True, 'data': ModerationActionSerializer(queryset[:100], many=True).data})


class AdminStatisticsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response({'success': True, 'data': {
            'reports_by_status': {row['status']: row['total'] for row in Report.objects.values('status').annotate(total=Count('id'))},
            'actions_by_type': {row['action']: row['total'] for row in ModerationAction.objects.values('action').annotate(total=Count('id'))},
            'games_by_status': {row['status']: row['total'] for row in Game.objects.values('status').annotate(total=Count('id'))},
        }})

class PlayerRatingsView(generics.ListCreateAPIView):
    permission_classes=[IsActiveAccount];serializer_class=PlayerRatingSerializer
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='ratings'
    def get_player(self): return get_object_or_404(User,public_id=self.kwargs['public_id'],is_active=True)
    def get_queryset(self): return PlayerRating.objects.filter(player=self.get_player(),deleted_at__isnull=True).select_related('reviewer','game').order_by('-created_at')
    def create(self,request,*args,**kwargs):
        s=PlayerRatingCreateSerializer(data=request.data);s.is_valid(raise_exception=True);rating=RatingService.rate_player(reviewer=request.user,player=self.get_player(),game=get_object_or_404(Game,public_id=s.validated_data['game_id']),rating=s.validated_data['rating'],review=s.validated_data.get('review',''));return Response({'success':True,'data':PlayerRatingSerializer(rating).data},status=201)
class PlayerStatisticsView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request,public_id,sport_id=None):
        user=get_object_or_404(User,public_id=public_id,is_active=True);sport=get_object_or_404(Sport,pk=sport_id) if sport_id else None
        return Response({'success':True,'data':PlayerStatisticsService.statistics(user=user,sport=sport) if not sport else PlayerStatisticsService.sport_statistics(user=user,sport=sport)})
class RatingDetailView(APIView):
    permission_classes=[IsActiveAccount]
    def patch(self,request,public_id):
        rating=get_object_or_404(PlayerRating,public_id=public_id,deleted_at__isnull=True)
        if rating.reviewer_id!=request.user.id: raise PermissionDenied('Only reviewer can edit.')
        s=PlayerRatingCreateSerializer(data={**request.data,'game_id':str(rating.game.public_id)});s.is_valid(raise_exception=True);rating.rating=s.validated_data['rating'];rating.review=s.validated_data.get('review','');rating.save(update_fields=['rating','review','updated_at']);return Response({'success':True,'data':PlayerRatingSerializer(rating).data})
    def delete(self,request,public_id):
        rating=get_object_or_404(PlayerRating,public_id=public_id,deleted_at__isnull=True)
        if rating.reviewer_id!=request.user.id and not request.user.is_staff: raise PermissionDenied('Not allowed.')
        rating.deleted_at=timezone.now();rating.save(update_fields=['deleted_at','updated_at']);return Response(status=204)
class LeaderboardView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request):
        sport=get_object_or_404(Sport,pk=request.query_params['sport']) if request.query_params.get('sport') else None;limit=min(int(request.query_params.get('limit',20)),100)
        users=LeaderboardService.ratings(sport=sport,city=request.query_params.get('city'),limit=limit);return Response({'success':True,'data':[{'player_id':str(u.public_id),'username':u.username,'average_rating':round(float(u.avg_rating),2),'rating_count':u.rating_count} for u in users]})

class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        queryset = Notification.objects.filter(user=self.request.user, deleted_at__isnull=True).select_related('actor').order_by('-created_at')
        if self.request.query_params.get('is_read') in ('true','false'): queryset=queryset.filter(is_read=self.request.query_params['is_read']=='true')
        if self.request.query_params.get('notification_type'): queryset=queryset.filter(type=self.request.query_params['notification_type'])
        if self.request.query_params.get('created_after'): queryset=queryset.filter(created_at__gte=self.request.query_params['created_after'])
        if self.request.query_params.get('created_before'): queryset=queryset.filter(created_at__lte=self.request.query_params['created_before'])
        return queryset

class NotificationDetailView(generics.RetrieveDestroyAPIView):
    permission_classes=[IsActiveAccount];serializer_class=NotificationSerializer;lookup_field='public_id'
    def get_queryset(self): return Notification.objects.filter(user=self.request.user,deleted_at__isnull=True)
    def perform_destroy(self,instance): NotificationService.delete_notification(notification=instance,user=self.request.user)
class NotificationReadView(APIView):
    permission_classes=[IsActiveAccount]
    def post(self,request,public_id):
        n=get_object_or_404(Notification,public_id=public_id,deleted_at__isnull=True);return Response({'success':True,'data':NotificationSerializer(NotificationService.mark_as_read(notification=n,user=request.user)).data})
class NotificationReadAllView(APIView):
    permission_classes=[IsActiveAccount]
    def post(self,request): return Response({'success':True,'updated':NotificationService.mark_all_as_read(user=request.user)})
class NotificationUnreadCountView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request): return Response({'success':True,'unread_count':NotificationService.unread_count(user=request.user)})
class NotificationPreferencesView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request):
        pref,_=NotificationPreference.objects.get_or_create(user=request.user);return Response({'success':True,'data':NotificationPreferenceSerializer(pref).data})
    def patch(self,request):
        pref,_=NotificationPreference.objects.get_or_create(user=request.user);s=NotificationPreferenceSerializer(pref,data=request.data,partial=True);s.is_valid(raise_exception=True);s.save();return Response({'success':True,'data':s.data})
class NotificationDevicesView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request): return Response({'success':True,'data':UserDeviceSerializer(UserDevice.objects.filter(user=request.user,is_active=True),many=True).data})
    def post(self,request):
        s=UserDeviceCreateSerializer(data=request.data);s.is_valid(raise_exception=True);device,created=DeviceService.register(user=request.user,**s.validated_data);return Response({'success':True,'created':created,'data':UserDeviceSerializer(device).data},status=201 if created else 200)
class NotificationDeviceDetailView(APIView):
    permission_classes=[IsActiveAccount]
    def delete(self,request,public_id):
        device=get_object_or_404(UserDevice,user=request.user,public_id=public_id);device.is_active=False;device.save(update_fields=['is_active','updated_at']);return Response(status=204)


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
    
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from math import radians, sin, cos, sqrt, atan2

from .models import Match, PlayerProfile


class AiMatchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        user_lat = user.latitude
        user_lng = user.longitude

        # fallback if no location
        matches = Match.objects.select_related('sport', 'ground', 'organizer').prefetch_related('joined_players').filter(
            status='upcoming', date_time__gte=timezone.now()
        ).annotate(players_count=Count('joined_players'))

        if user_lat is None or user_lng is None:
            return Response(MatchSerializer(matches.filter(players_count__lt=F('total_players'))[:10], many=True).data)

        matches = matches.filter(players_count__lt=F('total_players'))

        results = []

        for match in matches:
            if not match.ground:
                continue

            g = match.ground

            if not g.latitude or not g.longitude:
                continue

            distance = self.calculate_distance(
                user_lat, user_lng,
                g.latitude, g.longitude
            )

            # filter nearby (within 10 km)
            if distance <= 10:
                results.append((match, distance))

        # sort by nearest
        results.sort(key=lambda x: x[1])

        return Response(MatchSerializer([m[0] for m in results[:10]], many=True).data)

    def calculate_distance(self, lat1, lon1, lat2, lon2):
        R = 6371  # km

        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)

        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))

        return R * c


class AutoGameCreateView(APIView):
    """Create one compatible public Game from verified free court inventory."""
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'game_join'

    def post(self, request):
        serializer = AutoGameCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        radius = data.get('radius')
        if radius is None:
            radius = getattr(getattr(request.user, 'profile', None), 'preferred_distance', 15)
        game, created = AutoGameService.create(
            user=request.user,
            sport=data.get('sport'),
            radius=radius,
        )
        game = Game.objects.select_related('sport', 'venue', 'court__sport', 'host').prefetch_related(
            'game_players__user__user_sports__sport',
        ).get(pk=game.pk)
        return Response({
            'success': True,
            'created': created,
            'message': 'Game created from available court inventory.' if created else 'Your existing upcoming auto-created game is ready.',
            'data': GameSerializer(game, context={'request': request}).data,
        }, status=201 if created else 200)


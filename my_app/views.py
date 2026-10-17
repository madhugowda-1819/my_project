from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q, Count, F, Avg, Prefetch
from django.db import connection, transaction, IntegrityError
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
from rest_framework.exceptions import APIException, AuthenticationFailed, PermissionDenied, ValidationError
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import EmailMultiAlternatives, send_mail
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from urllib.parse import urlencode
import json
import hashlib
import hmac
import math
from uuid import UUID
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from .models import (
    Sport, UserSport, AvailabilitySlot, Match, Message, Notification, Ground, SportsGround, PlayerProfile,
    Venue, VenueSport, Court, CourtBlockedPeriod, VenueReview, CourtBooking, Game, GamePlayer, Conversation, ConversationMember, UserBlock,
    Community, CommunityMember, CommunityPost, CommunityComment, CommunityPostLike,
    Event, EventParticipant, EventReminder, Tournament, TournamentTeam, TournamentTeamMember, TournamentMatch, NotificationPreference, UserDevice, PlayerRating, Achievement, UserAchievement,
    College, CollegeMembership, CollegeChallenge, CollegeResultDispute, MatchTimePoll, MatchReflection,
    HealthActivityRecord,
    SportChallenge, ChallengeEntry,
)
from .serializers import (
    RegistrationRequestSerializer, LoginRequestSerializer, CurrentUserSerializer,
    UserSerializer, SportSerializer, PublicPlayerSerializer, UserSportSerializer,
    UserSportWriteSerializer, PlayerProfilePreferencesSerializer,
    NearbySearchSerializer, PlayerDirectoryQuerySerializer,
    PlayerRecommendationQuerySerializer, MatchMyMoodQuerySerializer,
    VenueSerializer, CourtSerializer, VenueDiscoveryQuerySerializer, VenueReviewCreateSerializer, VenueReviewSerializer,
    VenueAvailabilityQuerySerializer, CourtBookingCreateSerializer,
    BookingCreateSerializer, BookingSerializer, RazorpayVerificationSerializer,
    MatchSerializer, MatchParticipantSerializer, MessageSerializer,
    NotificationSerializer, AvailabilityToggleSerializer,
    GroundSerializer, GroundDiscoverySerializer, AutoGameCreateSerializer, MatchCreateSerializer, MatchUpdateSerializer,
    GameCreateSerializer, GameUpdateSerializer, GameQuerySerializer, GameRecommendationQuerySerializer,
    GameSerializer, GamePlayerSerializer, ConversationCreateSerializer,
    EventSerializer, CommunityPostSerializer,
    ConversationMessageCreateSerializer, ConversationMessageSerializer, LegacyMessageCreateSerializer,
    CommunityCreateSerializer, CommunityDiscoveryQuerySerializer, CommunityUpdateSerializer, CommunitySerializer, CommunityMemberSerializer,
    CommunityPostCreateSerializer, CommunityContentSerializer, CommunityPostSerializer, CommunityCommentSerializer,
    EventCreateSerializer, EventUpdateSerializer, EventSerializer, EventParticipantSerializer, EventReminderSerializer, TournamentCreateSerializer, TournamentUpdateSerializer, TournamentRegistrationSerializer, TournamentResultSerializer, TournamentSerializer, TournamentTeamSerializer, TournamentMatchSerializer,
    NotificationPreferenceSerializer, UserDeviceSerializer, UserDeviceCreateSerializer,
    PlayerRatingSerializer, PlayerRatingCreateSerializer, MatchReflectionSerializer, MatchReflectionInputSerializer,
    AchievementSerializer, UserAchievementSerializer,
    ClubMemberRoleSerializer, ClubInviteSerializer,
    CollegeSubmitSerializer, CollegeSerializer, CollegeMembershipSerializer,
    CollegeChallengeSerializer, CollegeChallengeCreateSerializer, ChallengeAnswerSerializer,
    CollegeTeamCreateSerializer, CollegeRosterAddSerializer, CollegeTeamMemberSerializer, CollegeResultDisputeSerializer,
    CollegeDisputeCreateSerializer, CollegeDisputeResolveSerializer,
    MatchTimePollCreateSerializer, MatchTimePollVoteSerializer, MatchTimePollFinalizeSerializer,
    PracticeCompletionInputSerializer, PracticeActivityWriteSerializer,
    PracticeActivityCreateSerializer, PracticeActivitySerializer,
    SportsGoalCreateSerializer, SportsGoalUpdateSerializer, SportsGoalTransitionSerializer,
    HealthActivityCreateSerializer, HealthActivityImportSerializer, HealthSourceDisconnectSerializer,
    TrainingRecommendationInputSerializer,
    ChallengeListQuerySerializer, ChallengeJoinSerializer,
    TrainingRecommendationInputSerializer,
)
from .maps import MapsProviderError, find_live_sports_grounds
from .permissions import ADMIN_GROUPS, IsActiveAccount, has_platform_role, VENUE_OWNER_GROUP
from .services.accounts import AuthenticationService, UserService
from .services.availability import VenueAvailabilityService
from .services.bookings import BookingCancellationService, BookingService
from .services.payments import PaymentService
from .services.games import ACTIVE_GAME_STATUSES, GameLifecycleService, GameService
from .services.game_matching import GameMatchingService, apply_mood_ranking
from .services.chat import (
    ChatPermissionService, ConversationService, MessageService, UnreadMessageService,
)
from .services.communities import (
    CommunityCommentService, CommunityDiscoveryService, CommunityMembershipService,
    CommunityModerationService, CommunityPostService, CommunityService,
)
from .services.events import EventService, EventRegistrationService, EventReminderService, EventCancellationService, EventCapacityService, TournamentService, TournamentRegistrationService, TournamentBracketService, TournamentMatchService, TournamentStandingsService
from .services.notifications import NotificationService, DeviceService
from .services.referrals import ReferralService
from .services.statistics import RatingService, PlayerStatisticsService, LeaderboardService
from .services.achievements import AchievementEvaluationService, AchievementService
from .services.challenges import ChallengeService
from .services.practice import PracticeService
from .services.goals import SportsGoalService
from .services.health import HealthActivityService
from .services.training_recommendations import build_training_plan
from .services.search import GlobalSearchService, VALID_TYPES
from .services.recommendation_engine import RecommendationProfileService, RecommendationService
from .services.locations import nearby_queryset
from .services.player_matching import PlayerMatchingService
from .services.colleges import CollegeService
from .services.match_time_polls import MatchTimePollService
from .services.players import PlayerService
from .services.venues import VenueDiscoveryService, VenueReviewService
from .services.ground_ingestion import GroundIngestionService
from .services.auto_games import AutoGameService
from .services.matches import cancel_match, join_match, leave_match, remove_player, update_match
from .utils.geo_utils import haversine_distance

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
.card{background:#fff;border:1px solid #eef1f6;border-radius:22px;padding:32px;box-shadow:0 18px 45px #102a4312}.eyebrow{color:var(--blue);font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}.card h1{font-size:30px;line-height:1.15;letter-spacing:-1px;margin:10px 0 12px}.card p{color:var(--muted);line-height:1.55;margin:0 0 24px}label{display:block;color:var(--navy);font-size:14px;font-weight:700;margin:17px 0 7px}.password-field{position:relative}.password-field input{padding-right:48px} .toggle-password{position:absolute;right:10px;top:50%;transform:translateY(-50%);width:auto;margin:0;padding:6px;background:transparent;color:var(--navy);font-size:18px}.toggle-password:hover{background:transparent}.app-link{display:block;margin-top:14px;padding:14px;border-radius:12px;background:#f0f6ff;color:#175cd3;font-size:14px;font-weight:700;text-align:center;text-decoration:none}input,button{width:100%;font:inherit;border-radius:12px}input{border:1px solid var(--border);padding:14px 15px;font-size:16px;outline:none}input:focus{border-color:var(--blue);box-shadow:0 0 0 4px #175cd31a}button{border:0;background:var(--blue);color:#fff;cursor:pointer;font-weight:800;padding:15px;margin-top:24px;transition:transform .15s,background .15s}button:hover{background:#124db2}button:active{transform:scale(.99)}button:disabled{opacity:.65;cursor:wait}.message{min-height:22px;margin:16px 0 0;color:#d92d20;font-size:14px;font-weight:600}.success{display:none;text-align:center;padding:4px 0 8px}.success-mark{width:72px;height:72px;margin:0 auto 18px;border-radius:50%;display:grid;place-items:center;background:#ecfdf3;color:var(--success);font-size:36px;font-weight:800}.success h1{margin-bottom:12px}.back-note{margin-top:24px;padding:13px;border-radius:12px;background:#f0f6ff;color:#175cd3;font-size:13px;font-weight:700;text-align:center}@media(max-width:420px){.page{padding-top:28px}.card{padding:25px 21px}.card h1{font-size:27px}}
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
    # Missing audience defaults to Player for older clients, but role checks
    # still prevent venue-owner/admin accounts from using the player login.
    audience = str(request.data.get('audience', 'player')).strip().lower()
    if audience not in {'player', 'venue', 'admin'}:
        raise ValidationError({'audience': ['Choose player, venue, or admin login.']})

    user = AuthenticationService.login(identifier=identifier, password=password)

    roles = set(user.groups.values_list('name', flat=True))
    # Login audiences are account roles, not incidental Django model
    # permissions. A player may have a limited staff permission without
    # becoming a platform administrator.
    is_admin = user.is_superuser or bool(roles & ADMIN_GROUPS)
    is_venue_owner = VENUE_OWNER_GROUP in roles
    is_player = PlayerProfile.objects.filter(user_id=user.id).exists()
    allowed = (
        (audience == 'admin' and is_admin)
        or (audience == 'venue' and is_venue_owner and not is_admin)
        # A venue owner who also has a player profile may use player features;
        # venue-only accounts must use Venue login. Admins remain exclusive.
        or (audience == 'player' and is_player and not is_admin)
    )
    if not allowed:
        if audience == 'player' and is_admin:
            detail = (
                'This account is assigned the SportMate Admin role. Use Admin login, '
                'or ask the application superowner to correct the role if this should be a player account.'
            )
        elif audience == 'player' and is_venue_owner:
            detail = 'This account must use Venue login.'
        elif audience == 'player':
            detail = 'This account does not have a player profile.'
        elif audience == 'venue':
            detail = 'This account does not have approved venue-owner access.'
        else:
            detail = 'This account does not have SportMate administrator access.'
        raise PermissionDenied(detail)

    # ✅ mark user online
    user.last_login = timezone.now()
    user.is_online = True
    user.save(update_fields=['last_login', 'is_online', 'last_seen', 'updated_at'])
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

    subject = "Reset your SportMate password"
    text = f"Hello {user.get_full_name() or user.username},\n\nReset your SportMate password here: {reset_link}\n\nThis link expires shortly. If you did not request this, ignore this email."
    html = f'''<!doctype html><html><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#102a43"><div style="max-width:560px;margin:30px auto;padding:28px;background:#fff;border-radius:18px;border:1px solid #e6ebf2"><div style="text-align:center"><div style="display:inline-block;background:#102a43;border:3px solid #8eea27;border-radius:16px;padding:10px;color:#fff;font-size:22px;font-weight:800">Sport<span style="color:#8eea27">Mate</span></div><h1 style="font-size:24px;margin:24px 0 8px">Reset your password</h1><p style="color:#667085">Hello {user.get_full_name() or user.username}, we received a request to reset your SportMate account password.</p><a href="{reset_link}" style="display:inline-block;margin:18px 0;padding:14px 24px;background:#175cd3;color:#fff;text-decoration:none;border-radius:10px;font-weight:700">Reset password</a><p style="font-size:13px;color:#667085">This secure link expires shortly. If you did not request this, you can safely ignore this email.</p><p style="font-size:12px;color:#98a2b3;word-break:break-all">If the button does not work:<br>{reset_link}</p></div></div></body></html>'''
    mail = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [email])
    mail.attach_alternative(html, 'text/html')
    mail.send()

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

    def get(self, request):
        sports = request.user.user_sports.select_related('sport').order_by('-preferred', 'sport__name')
        return Response({'success': True, 'data': UserSportSerializer(sports, many=True).data})

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


class MyReferralsView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        return Response({'success': True, 'data': ReferralService.private_summary(request.user)})


class PlayerDetailView(generics.RetrieveAPIView):
    serializer_class = PublicPlayerSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        blocked_ids = UserBlock.objects.filter(user=self.request.user).values('blocked_user_id')
        blocked_by_ids = UserBlock.objects.filter(blocked_user=self.request.user).values('user_id')
        return User.objects.filter(
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        ).filter(
            Q(profile__profile_visibility='public') | Q(pk=self.request.user.pk)
        ).exclude(pk__in=blocked_ids).exclude(pk__in=blocked_by_ids).prefetch_related(
            'user_sports__sport')


class PlayerPublicIdDetailView(PlayerDetailView):
    lookup_field = 'public_id'
    lookup_url_kwarg = 'public_id'


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
def _with_public_player_rating_summary(queryset):
    """Fetch public rating aggregates with the player rows, not N+1 queries."""
    return queryset.annotate(
        public_rating_count=Count(
            'ratings_received',
            filter=Q(ratings_received__deleted_at__isnull=True),
            distinct=True,
        ),
        public_average_rating=Avg(
            'ratings_received__rating',
            filter=Q(ratings_received__deleted_at__isnull=True),
        ),
        public_performance=Avg(
            'ratings_received__playing_performance',
            filter=Q(ratings_received__deleted_at__isnull=True),
        ),
        public_teamwork=Avg(
            'ratings_received__teamwork',
            filter=Q(ratings_received__deleted_at__isnull=True),
        ),
        public_sportsmanship=Avg(
            'ratings_received__sportsmanship',
            filter=Q(ratings_received__deleted_at__isnull=True),
        ),
        public_punctuality=Avg(
            'ratings_received__punctuality',
            filter=Q(ratings_received__deleted_at__isnull=True),
        ),
    )


class PlayerListView(generics.ListAPIView):
    serializer_class = PublicPlayerSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        params = PlayerDirectoryQuerySerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        data = params.validated_data
        queryset = User.objects.filter(
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
            profile__profile_visibility='public',
        ).exclude(pk=self.request.user.pk)
        blocked_ids = UserBlock.objects.filter(user=self.request.user).values('blocked_user_id')
        blocked_by_ids = UserBlock.objects.filter(blocked_user=self.request.user).values('user_id')
        queryset = queryset.exclude(pk__in=blocked_ids).exclude(pk__in=blocked_by_ids)

        if data['availability'] == 'available':
            queryset = queryset.filter(is_available=True)
        elif data['availability'] == 'unavailable':
            queryset = queryset.filter(is_available=False)
        if data.get('q'):
            query = data['q']
            queryset = queryset.filter(Q(full_name__icontains=query) | Q(username__icontains=query))
        if data.get('city'):
            queryset = queryset.filter(city__icontains=data['city'])
        sport = data.get('sport')
        skill_level = data.get('skill_level')
        if sport and skill_level:
            queryset = queryset.filter(
                Q(user_sports__sport__slug__iexact=sport)
                | Q(user_sports__sport__name__iexact=sport),
                user_sports__skill_level=skill_level,
                user_sports__sport__is_active=True,
            )
        elif sport:
            queryset = queryset.filter(
                Q(user_sports__sport__slug__iexact=sport)
                | Q(user_sports__sport__name__iexact=sport),
                user_sports__sport__is_active=True,
            )
        elif skill_level:
            queryset = queryset.filter(
                user_sports__skill_level=skill_level,
                user_sports__sport__is_active=True,
            )
        queryset = _with_public_player_rating_summary(queryset).prefetch_related(
            'user_sports__sport')

        has_coordinates = 'latitude' in data and 'longitude' in data
        sort = data.get('sort', 'distance' if has_coordinates else 'name')
        if has_coordinates:
            queryset = nearby_queryset(
                queryset, latitude=data['latitude'], longitude=data['longitude'],
                radius=data['radius'],
            )
        if sort == 'rating':
            queryset = queryset.order_by(
                F('public_average_rating').desc(nulls_last=True),
                '-public_rating_count', 'username',
            )
        elif sort == 'name' or not has_coordinates:
            queryset = queryset.order_by('full_name', 'username')
        # nearby_queryset already orders by computed distance.
        return queryset.distinct()


class NearbyPlayerView(generics.ListAPIView):
    serializer_class = PublicPlayerSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        params = NearbySearchSerializer(data=self.request.query_params)
        params.is_valid(raise_exception=True)
        data = params.validated_data
        blocked_ids = UserBlock.objects.filter(user=self.request.user).values('blocked_user_id')
        blocked_by_ids = UserBlock.objects.filter(blocked_user=self.request.user).values('user_id')
        queryset = _with_public_player_rating_summary(User.objects.filter(
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
            is_available=True,
            profile__profile_visibility='public',
        ).exclude(pk=self.request.user.pk).exclude(pk__in=blocked_ids).exclude(
            pk__in=blocked_by_ids,
        )).prefetch_related('user_sports__sport')
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


class MatchMyMoodView(APIView):
    """Combined, deterministic mood-aware recommendations for real players and games."""
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'recommendations'

    def get(self, request):
        if not hasattr(request.user, 'profile'):
            raise PermissionDenied('Player profile is required to use Match My Mood.')
        query = MatchMyMoodQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        profile = request.user.profile
        selected_mood = data.get('mood', profile.matching_mood)
        # Recommendations are a read operation. Preference edits are persisted
        # by the profile endpoint, not as a side effect of GET/query filters.
        latitude = data.get('latitude', request.user.latitude)
        longitude = data.get('longitude', request.user.longitude)
        if latitude is None or longitude is None:
            latitude = longitude = None

        sports = request.user.user_sports.select_related('sport').filter(sport__is_active=True)
        sport = data.get('sport')
        if sport:
            sports = sports.filter(sport=sport)
        elif sports.filter(preferred=True).exists():
            sports = sports.filter(preferred=True)
        sport_entries = list(sports)
        requested_skill = data.get('skill_level')
        if requested_skill:
            sport_entries = [entry for entry in sport_entries if entry.skill_level == requested_skill]
        if not sport_entries:
            return Response({'success': True, 'mood': selected_mood, 'games': [], 'players': []})

        from .services.player_matching import PlayerMatchingService
        from .services.locations import validate_coordinates

        game_results = []
        player_results = []
        try:
            mood_weight = max(0, min(20, int(getattr(settings, 'MATCH_MOOD_RANKING_WEIGHTS', {}).get('mood', 10))))
        except (TypeError, ValueError):
            mood_weight = 10
        for user_sport in sport_entries:
            if latitude is not None:
                lat, lng, radius = validate_coordinates(latitude, longitude, data['radius'])
            else:
                lat = lng = None
                radius = data['radius']

            games = GameMatchingService.get_recommendations(
                user=request.user, sport=user_sport.sport, latitude=lat, longitude=lng,
                radius=radius, date=data.get('date'), skill_level=data.get('skill_level'),
                limit=data['limit'],
            )
            if data['availability'] != 'any':
                if data['availability'] == 'weekend':
                    games = [item for item in games if item['game'].game_date.weekday() >= 5]
                else:
                    games = [item for item in games if GameMatchingService._time_band(item['game'].start_time) == data['availability']]
                games = [
                    item for item in games
                    if not getattr(item['game'].host, 'profile', None)
                    or not item['game'].host.profile.preferred_play_times
                    or data['availability'] in item['game'].host.profile.preferred_play_times
                ]
            game_results.extend(games)

            if lat is not None:
                players = PlayerMatchingService.recommend(
                    requester=request.user, sport=user_sport.sport, latitude=lat,
                    longitude=lng, radius=radius, limit=data['limit'],
                    availability=data['availability'], skill_level=data.get('skill_level'),
                )
                player_results.extend(players)

        game_results = apply_mood_ranking(
            game_results, requester_mood=selected_mood,
            mood_getter=lambda item: getattr(item['game'].host, 'profile', None).matching_mood if hasattr(item['game'].host, 'profile') else '',
            base_key='match_score', reasons_key='reasons',
        )[:data['limit']]
        for item in player_results:
            if hasattr(item['score'], 'score'):
                item['score'] = item['score'].score
        player_results = apply_mood_ranking(
            player_results, requester_mood=selected_mood,
            mood_getter=lambda item: item['candidate'].profile.matching_mood if hasattr(item['candidate'], 'profile') else '',
            base_key='score', reasons_key='reasons',
        )[:data['limit']]
        return Response({
            'success': True,
            'algorithm': 'weighted_compatibility_with_capped_mood_signal_v1',
            'mood': selected_mood,
            'ranking_formula': f'final = round(base_score * (1 - {mood_weight}/100) + mood_affinity * {mood_weight}/100) for games and players. Mood weight defaults to 10% and is capped at 20%; eligibility is evaluated before ranking.',
            'games': [
                {'game': GameSerializer(item['game'], context={'request': request}).data,
                 'score': item['match_score'], 'distance_km': item['distance_km'],
                 'reasons': item['reasons']}
                for item in game_results
            ],
            'players': [
                {'player': PublicPlayerSerializer(item['candidate'], context={'request': request}).data,
                 'score': item['score'], 'distance_km': round(item['candidate'].distance_km, 2),
                 'sport': item['sport'].sport.slug, 'reasons': item['reasons']}
                for item in player_results
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
        return Venue.objects.filter(active=True, is_demo=False, courts__active=True).distinct().prefetch_related(
            'venue_sports__sport',
            Prefetch('courts', queryset=Court.objects.filter(active=True).select_related('sport')),
            'images', 'reviews__user', 'amenities',
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
        # Inactive venues still have a structured availability response with
        # every slot marked closed. Booking creation has its own authoritative
        # active-venue check, so this read path cannot make a venue bookable.
        venue = get_object_or_404(Venue.objects.filter(
            is_demo=False, courts__active=True,
        ).distinct(), public_id=public_id)
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
        queryset = CourtBooking.objects.filter(user=self.request.user).select_related('venue', 'court', 'payment')
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
            queryset = queryset.exclude(status=CourtBooking.Status.CANCELLED).filter(ends_at__gt=timezone.now())
        if self.request.query_params.get('past') == 'true':
            queryset = queryset.exclude(status=CourtBooking.Status.CANCELLED).filter(ends_at__lte=timezone.now())
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
        return CourtBooking.objects.filter(user=self.request.user).select_related('venue', 'court', 'payment')


class BookingCancelView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        booking = get_object_or_404(CourtBooking.objects.select_related('venue'), public_id=public_id)
        booking = BookingCancellationService.cancel(actor=request.user, booking=booking, reason=request.data.get('reason', ''))
        return Response({'success': True, 'data': BookingSerializer(booking).data})


class PaymentStartView(APIView):
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'booking'

    def post(self, request):
        serializer = BookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, key_id = PaymentService.start(user=request.user, booking_data=serializer.validated_data)
        return Response({'success': True, 'data': {
            'key_id': key_id, 'order_id': payment.provider_order_id, 'amount': payment.amount_paise,
            'currency': payment.currency, 'booking': BookingSerializer(payment.booking).data,
        }}, status=201)


class PaymentVerifyView(APIView):
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'booking'

    def post(self, request):
        serializer = RazorpayVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = PaymentService.verify_checkout(user=request.user, **{
            'provider_order_id': serializer.validated_data['razorpay_order_id'],
            'provider_payment_id': serializer.validated_data['razorpay_payment_id'],
            'signature': serializer.validated_data['razorpay_signature'],
        })
        return Response({'success': True, 'data': {'payment_status': payment.status, 'booking': BookingSerializer(payment.booking).data}})


class RazorpayWebhookView(APIView):
    """Public endpoint authenticated with Razorpay's webhook HMAC signature."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        signature = request.headers.get('X-Razorpay-Signature', '')
        expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest() if secret else ''
        if not secret or not hmac.compare_digest(expected, signature):
            return Response({'detail': 'Invalid webhook signature.'}, status=400)
        try:
            payload = json.loads(request.body.decode('utf-8'))
            payment_entity = payload['payload']['payment']['entity']
            order_id = payment_entity['order_id']
            event = payload['event']
            if event == 'payment.captured':
                PaymentService.capture(provider_order_id=order_id, provider_payment_id=payment_entity['id'])
            elif event == 'payment.failed':
                PaymentService.fail(provider_order_id=order_id, reason=payment_entity.get('error_description', 'Payment failed.'))
        except (KeyError, ValueError):
            return Response({'detail': 'Invalid webhook payload.'}, status=400)
        except Exception:
            # A duplicate or late event should be retried by Razorpay, not exposed to clients.
            return Response({'detail': 'Webhook processing failed.'}, status=500)
        return Response({'success': True})


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
                    'longitude': ground.longitude, 'distance_km': round(distance, 2),
                    'address': None, 'source': 'openstreetmap_live',
                    'attribution': '© OpenStreetMap contributors',
                    'booking_supported': False,
                    'verification_status': 'external_unverified'}
                   for ground, distance in grounds]
        return Response({
            'count': len(payload), 'radius_km': self.radius_km,
            'attribution': '© OpenStreetMap contributors', 'grounds': payload,
        })

    @staticmethod
    def _refresh_cache_key(latitude, longitude):
        # 0.2 degree cells are smaller than the search diameter while still
        # absorbing normal GPS movement around the same user area.
        return f'osm-ground-refresh:{latitude:.1f}:{longitude:.1f}'

    def _nearby_cached(self, latitude, longitude):
        lat_delta = self.radius_km / 111.0
        lon_delta = self.radius_km / max(111.0 * math.cos(math.radians(latitude)), 0.000001)
        candidates = SportsGround.objects.exclude(osm_id__startswith='google/').filter(
            latitude__range=(latitude - lat_delta, latitude + lat_delta),
            longitude__range=(longitude - lon_delta, longitude + lon_delta),
        )
        grounds = [(ground, haversine_distance(latitude, longitude, ground.latitude, ground.longitude))
                   for ground in candidates]
        return sorted((item for item in grounds if item[1] <= self.radius_km), key=lambda item: item[1])

    def _refresh_from_overpass(self, latitude, longitude):
        query = ('[out:json][timeout:30];('
                 f'nwr["leisure"~"^(pitch|sports_centre|stadium)$"]["name"](around:20000,{latitude},{longitude});'
                 f'nwr["sport"~"^(cricket|football|soccer|badminton|tennis|table_tennis|volleyball|pickleball|basketball)$",i]["name"](around:20000,{latitude},{longitude});'
                 f'nwr["club"="sport"]["name"](around:20000,{latitude},{longitude});'
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
            name = str(tags.get('name') or '').strip()
            if not name:
                continue
            SportsGround.objects.update_or_create(
                osm_id=f"{element.get('type', 'feature')}/{element.get('id')}",
                defaults={'name': name,
                          'ground_type': str(tags.get('leisure') or 'sports_ground'),
                          'sport': str(tags.get('sport') or tags.get('sport:1') or 'multi-sport'),
                          'latitude': element_latitude, 'longitude': element_longitude},
            )

class SportsGroundPhotoView(APIView):
    """Legacy route retained without exposing stored provider photo content."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, ground_id):
        return Response(
            {'detail': 'Provider photos are not retained by SportMate.'},
            status=410,
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

        # Older app versions persisted Places results in Ground rows. Keep
        # those rows intact for existing references, but stop serving them as
        # the current discovery catalogue.
        grounds = Ground.objects.exclude(
            source__in=('google_maps_live', 'google_places_live'),
        ).exclude(
            maps_place_id__startswith='google_maps_live:',
        ).prefetch_related('sports')
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'search'

    def get(self, request):
        raw_latitude, raw_longitude = request.GET.get('lat'), request.GET.get('lng')
        if bool(raw_latitude) != bool(raw_longitude):
            return Response({'detail': 'lat and lng must be provided together.'}, status=400)
        try:
            latitude = float(raw_latitude) if raw_latitude else None
            longitude = float(raw_longitude) if raw_longitude else None
            radius = float(request.GET.get('radius', 10))
        except (TypeError, ValueError):
            return Response({'detail': 'Coordinates and radius must be valid numbers.'}, status=400)
        if latitude is not None and (not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
            return Response({'detail': 'Coordinates are outside their valid range.'}, status=400)
        if not 0 < radius <= 50:
            return Response({'detail': 'radius must be between 0 and 50 km.'}, status=400)
        query = request.GET.get('query', '').strip()
        sport = request.GET.get('sport', '').strip()
        category = request.GET.get('category', '').strip().lower()
        if len(query) > 120 or len(sport) > 60:
            return Response({'detail': 'Search text is too long.'}, status=400)
        categories = {'turf', 'court', 'stadium', 'sports centre', 'sports center', 'sports complex'}
        if category and category not in categories:
            return Response({'detail': 'Unsupported sports facility category.'}, status=400)
        if latitude is None and not query and not sport and not category:
            return Response({'detail': 'Provide coordinates or a venue, area, or sport search.'}, status=400)
        try:
            places = find_live_sports_grounds(
                latitude=latitude, longitude=longitude,
                sport=sport or None, query=query or None,
                category=category or None,
                radius_km=radius,
            )
        except MapsProviderError as error:
            return Response({'detail': str(error)}, status=503)
        results = []
        for place in places[:20]:
            name = str(place.get('name') or '').strip()
            try:
                place_latitude, place_longitude = float(place.get('latitude')), float(place.get('longitude'))
            except (TypeError, ValueError):
                continue
            if not name:
                continue
            distance = None
            if latitude is not None:
                distance = haversine_distance(latitude, longitude, place_latitude, place_longitude)
                # A named venue search should be allowed to find the exact
                # place even when it is outside the nearby-radius filter.
                # Radius remains enforced for location/category-only searches.
                if not query and distance > radius:
                    continue
            normalized = {
                'id': place.get('id'), 'source_id': place.get('id'),
                'name': name, 'address': place.get('address'),
                'latitude': place_latitude, 'longitude': place_longitude,
                'sports': [], 'category': (place.get('types') or [None])[0],
                'source': place.get('source'), 'maps_url': place.get('mapsUrl'),
                'website_url': place.get('websiteUrl'),
                'verification_status': 'external_unverified',
                'booking_supported': False,
            }
            if distance is not None:
                normalized['distance_km'] = round(distance, 2)
            results.append(normalized)
        results.sort(key=lambda item: item.get('distance_km', float('inf')))
        return Response({
            'count': len(results),
            'results': results,
            'booking_supported': False,
            'verification_status': 'external_unverified',
        })


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
        # Google Places content is not persisted in the SportMate catalogue.
        # OSM records remain eligible for the existing ground/match workflow.
        places = [place for place in places if place.get('source') == 'openstreetmap_live']
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
    serializer_class = MatchSerializer
    permission_classes = [IsActiveAccount]

    def get_queryset(self):
        queryset = Match.objects.select_related('sport', 'ground', 'organizer').prefetch_related('joined_players')
        sport = self.request.query_params.get('sport', '').strip()
        if sport:
            queryset = queryset.filter(Q(sport__name__iexact=sport) | Q(sport__slug__iexact=sport))
        if self.request.query_params.get('upcoming', '').lower() in ('1', 'true', 'yes'):
            queryset = queryset.filter(status='upcoming', date_time__gt=timezone.now())
        return queryset.order_by('date_time')


class MatchHistoryView(generics.ListAPIView):
    permission_classes = [IsActiveAccount]
    serializer_class = MatchSerializer

    def get_queryset(self):
        return Match.objects.filter(participation_history__user=self.request.user).select_related(
            'sport', 'ground', 'organizer',
        ).prefetch_related('joined_players').distinct().order_by('-date_time')


class CreateMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        serializer = MatchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        match = serializer.save(organizer=request.user)
        return Response({'match': MatchSerializer(match).data}, status=201)


class MatchDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, match_id):
        match = get_object_or_404(
            Match.objects.select_related('sport', 'ground', 'organizer').prefetch_related(
                'joined_players', 'participation_history',
            ),
            pk=match_id,
        )
        return Response({
            'success': True,
            'match': MatchSerializer(match, context={'request': request}).data,
        })

    def patch(self, request, match_id):
        match = get_object_or_404(Match, pk=match_id)
        serializer = MatchUpdateSerializer(data=request.data, context={'match': match})
        serializer.is_valid(raise_exception=True)
        match = update_match(match_id=match_id, organizer=request.user, changes=serializer.validated_data)
        return Response({'success': True, 'match': MatchSerializer(match, context={'request': request}).data})


class MatchPublicDetailView(APIView):
    """Public-UUID lookup used by persisted notification deep links."""
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        match = get_object_or_404(
            Match.objects.select_related('sport', 'ground', 'organizer').prefetch_related(
                'joined_players', 'participation_history',
            ),
            public_id=public_id,
        )
        return Response({'success': True, 'match': MatchSerializer(match, context={'request': request}).data})

class JoinMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, match_id):
        match, joined = join_match(match_id=match_id, user=request.user)
        return Response({
            'success': True,
            'message': 'Joined successfully' if joined else 'Already joined this match.',
            'match': MatchSerializer(match, context={'request': request}).data,
        })


class LeaveMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, match_id):
        match = leave_match(match_id=match_id, user=request.user)
        return Response({'success': True, 'message': 'You left the match.', 'match': MatchSerializer(match, context={'request': request}).data})


class CancelMatchView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, match_id):
        match = cancel_match(match_id=match_id, organizer=request.user)
        return Response({'success': True, 'message': 'Match cancelled.', 'match': MatchSerializer(match, context={'request': request}).data})


class MatchRescueOpportunityListView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        from my_app.serializers import MatchReplacementInvitationSerializer
        from my_app.services.matches import rescue_opportunities_for
        invitations = rescue_opportunities_for(user=request.user)
        return Response({'success': True, 'data': MatchReplacementInvitationSerializer(invitations, many=True).data})


class MatchRescueAcceptView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        from my_app.services.matches import accept_rescue_invitation
        match, accepted = accept_rescue_invitation(invitation_id=public_id, user=request.user)
        return Response({
            'success': True,
            'message': 'You accepted the replacement slot.' if accepted else 'You are already in this match.',
            'match': MatchSerializer(match, context={'request': request}).data,
        })


class MatchRescueDeclineView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        from my_app.services.matches import decline_rescue_invitation
        invitation = decline_rescue_invitation(invitation_id=public_id, user=request.user)
        from my_app.serializers import MatchReplacementInvitationSerializer
        return Response({
            'success': True,
            'message': 'You declined the replacement invitation.',
            'data': MatchReplacementInvitationSerializer(invitation).data,
        })


class DailyPracticeView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        timezone_name = request.query_params.get('timezone', 'UTC')
        today = PracticeService.current_date(timezone_name)
        try:
            practice_date = date.fromisoformat(request.query_params.get('date', today.isoformat()))
        except ValueError as exc:
            raise ValidationError({'date': ['Use YYYY-MM-DD format.']}) from exc
        data = PracticeService.get_day(
            user=request.user, practice_date=practice_date,
            timezone_name=timezone_name,
        )
        return Response({'success': True, 'data': data})


class DailyPracticeCompletionView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request, public_id):
        serializer = PracticeCompletionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = PracticeService.set_completion(
            user=request.user, activity_id=public_id,
            practice_date=serializer.validated_data['date'],
            timezone_name=serializer.validated_data['timezone'],
            completed=serializer.validated_data['completed'],
        )
        return Response({'success': True, 'data': data})


class DailyPracticeActivitiesView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        from my_app.services.practice import ensure_default_activities
        ensure_default_activities(request.user)
        from my_app.models import PracticeActivity
        activities = PracticeActivity.objects.filter(user=request.user).order_by('sort_order', 'id')
        return Response({'success': True, 'data': PracticeActivitySerializer(activities, many=True).data})

    def post(self, request):
        serializer = PracticeActivityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        activity = PracticeService.create_activity(user=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': PracticeActivitySerializer(activity).data}, status=201)

class DailyPracticeActivityDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request, public_id):
        serializer = PracticeActivityWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        activity = PracticeService.update_activity(user=request.user, public_id=public_id, changes=serializer.validated_data)
        return Response({'success': True, 'data': PracticeActivitySerializer(activity).data})


class DailyPracticeHistoryView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        timezone_name = request.query_params.get('timezone', 'UTC')
        today = PracticeService.current_date(timezone_name).isoformat()
        try:
            start = date.fromisoformat(request.query_params.get('start', today))
            end = date.fromisoformat(request.query_params.get('end', today))
        except ValueError as exc:
            raise ValidationError({'date': ['Use YYYY-MM-DD format.']}) from exc
        data = PracticeService.history(
            user=request.user, start_date=start, end_date=end,
            timezone_name=timezone_name,
        )
        return Response({'success': True, 'data': data})


class SportsGoalListCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        return Response({'success': True, 'data': SportsGoalService.list_for(user=request.user)})

    def post(self, request):
        serializer = SportsGoalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = SportsGoalService.create(user=request.user, data=serializer.validated_data)
        return Response({'success': True, 'data': data}, status=201)


class SportsGoalDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request, public_id):
        serializer = SportsGoalUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = SportsGoalService.update(user=request.user, public_id=public_id, changes=serializer.validated_data)
        return Response({'success': True, 'data': data})

    def post(self, request, public_id):
        serializer = SportsGoalTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = SportsGoalService.transition(user=request.user, public_id=public_id, status=serializer.validated_data['status'])
        return Response({'success': True, 'data': data})


class SportsGoalSummaryView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        timezone_name = request.query_params.get('timezone', 'Asia/Kolkata')
        return Response({'success': True, 'data': SportsGoalService.summaries(
            user=request.user, timezone_name=timezone_name,
        )})


class TrainingRecommendationView(APIView):
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'recommendations'

    def post(self, request):
        serializer = TrainingRecommendationInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data.copy()
        values['timezone_name'] = values.pop('timezone', 'Asia/Kolkata')
        plan = build_training_plan(user=request.user, **values)
        return Response({'success': True, 'data': plan})


class HealthActivityListCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        timezone_name = request.query_params.get('timezone', 'Asia/Kolkata')
        try:
            today = timezone.now().astimezone(ZoneInfo(timezone_name)).date()
        except (ZoneInfoNotFoundError, TypeError):
            raise ValidationError({'timezone': ['Use a valid IANA timezone.']})
        try:
            end = date.fromisoformat(request.query_params.get('end', today.isoformat()))
            start = date.fromisoformat(request.query_params.get('start', (end - timedelta(days=6)).isoformat()))
        except ValueError as exc:
            raise ValidationError({'date': ['Use YYYY-MM-DD format.']}) from exc
        data = HealthActivityService.summary(user=request.user, start=start, end=end, timezone_name=timezone_name)
        return Response({'success': True, 'data': data})

    def post(self, request):
        serializer = HealthActivityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data.copy()
        values['timezone_name'] = values.pop('timezone', 'Asia/Kolkata')
        record = HealthActivityService.create_manual(user=request.user, **values)
        return Response({'success': True, 'data': HealthActivityService.serialize_record(record)}, status=201)


class HealthActivityRecordView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        record = get_object_or_404(
            HealthActivityRecord, user=request.user, public_id=public_id,
        )
        return Response({'success': True, 'data': HealthActivityService.serialize_record(record)})

    def delete(self, request, public_id):
        if not HealthActivityService.delete_record(user=request.user, public_id=public_id):
            return Response({'success': False, 'detail': 'Manual health record not found.'}, status=404)
        return Response(status=204)


class HealthActivitySyncView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        serializer = HealthActivityImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = HealthActivityService.sync_import(user=request.user, source=data['source'], records=data['records'])
        return Response({'success': True, 'data': result})


class HealthSourceDisconnectView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request):
        serializer = HealthSourceDisconnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deleted = HealthActivityService.disconnect_source(user=request.user, source=serializer.validated_data['source'])
        return Response({'success': True, 'deleted_records': deleted, 'connected': False})


class MatchParticipantsView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, match_id):
        match = get_object_or_404(Match.objects.prefetch_related('joined_players'), pk=match_id)
        return Response({'success': True, 'data': MatchParticipantSerializer(match.joined_players.all(), many=True).data})


class MatchTimePollListCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, match_id):
        match = get_object_or_404(Match, pk=match_id)
        if match.organizer_id != request.user.id and not match.joined_players.filter(pk=request.user.pk).exists():
            raise PermissionDenied('Only match participants can view schedule polls.')
        poll = match.time_polls.order_by('-created_at').first()
        if not poll:
            return Response({'success': True, 'data': None})
        return Response({'success': True, 'data': MatchTimePollService.serialize(poll, user=request.user)})

    def post(self, request, match_id):
        serializer = MatchTimePollCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        poll = MatchTimePollService.create(match_id=match_id, actor=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': MatchTimePollService.serialize(poll, user=request.user)}, status=201)


class MatchTimePollVoteView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        serializer = MatchTimePollVoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        MatchTimePollService.vote(poll_id=public_id, actor=request.user, **serializer.validated_data)
        poll = get_object_or_404(MatchTimePoll.objects.select_related('match', 'creator', 'finalized_option'), public_id=public_id)
        return Response({'success': True, 'data': MatchTimePollService.serialize(poll, user=request.user)})


class MatchTimePollFinalizeView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        serializer = MatchTimePollFinalizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        poll, selected = MatchTimePollService.finalize(poll_id=public_id, actor=request.user, **serializer.validated_data)
        poll = get_object_or_404(MatchTimePoll.objects.select_related('match', 'creator', 'finalized_option'), public_id=public_id)
        match = get_object_or_404(Match.objects.select_related('sport', 'ground', 'organizer'), pk=poll.match_id)
        return Response({'success': True, 'tie_requires_decision': selected is None, 'data': MatchTimePollService.serialize(poll, user=request.user), 'match': MatchSerializer(match, context={'request': request}).data})


class MatchTimePollCloseView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        poll = MatchTimePollService.close(poll_id=public_id, actor=request.user)
        poll = get_object_or_404(MatchTimePoll.objects.select_related('match', 'creator', 'finalized_option'), pk=poll.pk)
        return Response({'success': True, 'data': MatchTimePollService.serialize(poll, user=request.user)})


class RemoveMatchParticipantView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, match_id, user_id):
        reason = str(request.data.get('reason', '')).strip()
        if len(reason) > 500:
            raise ValidationError({'reason': ['Reason cannot exceed 500 characters.']})
        match = remove_player(match_id=match_id, organizer=request.user, player_id=user_id, reason=reason)
        return Response({'success': True, 'message': 'Player removed from the match.', 'match': MatchSerializer(match, context={'request': request}).data})


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


class GamePlayerRemoveView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id, user_id):
        reason = str(request.data.get('reason', '')).strip()
        if len(reason) > 500:
            raise ValidationError({'reason': ['Reason cannot exceed 500 characters.']})
        game = GameService.remove_player(
            game_id=public_id, actor=request.user, player_id=user_id, reason=reason,
        )
        return Response({'success': True, 'status': game.status, 'message': 'Player removed from the game.'})


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
                'metadata': {'sport': tournament.sport.name, 'start_date': tournament.start_date.isoformat(),
                             'venue': tournament.venue.name if tournament.venue_id else tournament.nearby_ground.name if tournament.nearby_ground_id else None}}


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
def _calendar_date(value, name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({name: 'Use YYYY-MM-DD.'}) from exc


def _calendar_filters(request, *, tournaments=False):
    date_from = _calendar_date(request.query_params.get('date_from'), 'date_from')
    date_to = _calendar_date(request.query_params.get('date_to'), 'date_to')
    if date_from and date_to and date_to < date_from:
        raise ValidationError({'date_to': 'Must be on or after date_from.'})
    sport_id = request.query_params.get('sport_id') or request.query_params.get('sport')
    city = (request.query_params.get('location') or request.query_params.get('city') or '').strip()
    status = request.query_params.get('status')
    lifecycle = request.query_params.get('lifecycle')
    registration_status = request.query_params.get('registration_status')
    if registration_status not in (None, '', 'registered', 'waitlisted', 'not_registered'):
        raise ValidationError({'registration_status': 'Use registered, waitlisted, or not_registered.'})
    if lifecycle not in (None, '', 'upcoming', 'ongoing', 'past'):
        raise ValidationError({'lifecycle': 'Use upcoming, ongoing, or past.'})
    if tournaments:
        if registration_status == 'waitlisted': raise ValidationError({'registration_status':'Tournament registration status must be registered or not_registered.'})
        if status and status not in Tournament.Status.values: raise ValidationError({'status':'Invalid tournament status.'})
        qs = Tournament.objects.select_related('sport', 'venue', 'nearby_ground', 'organizer').order_by('start_date')
        if date_from: qs = qs.filter(end_date__gte=date_from)
        if date_to: qs = qs.filter(start_date__lte=date_to)
        elif not date_from: qs = qs.filter(end_date__gte=timezone.localdate())
        today = timezone.localdate()
        if lifecycle == 'upcoming': qs = qs.filter(start_date__gt=today)
        elif lifecycle == 'ongoing': qs = qs.filter(start_date__lte=today, end_date__gte=today)
        elif lifecycle == 'past': qs = qs.filter(end_date__lt=today)
        if sport_id:
            if not str(sport_id).isdigit(): raise ValidationError({'sport_id': 'Must be a valid sport id.'})
            qs = qs.filter(sport_id=sport_id)
        if city: qs = qs.filter(venue__city__icontains=city)
        if status: qs = qs.filter(status=status)
        if registration_status:
            mine = Q(teams__members__user=request.user, teams__members__status=TournamentTeamMember.Status.ACTIVE)
            qs = qs.filter(mine) if registration_status == 'registered' else qs.exclude(mine)
        return qs.distinct()
    qs = Event.objects.filter(Q(visibility=Event.Visibility.PUBLIC) | Q(organizer=request.user) | Q(participants__user=request.user) | Q(community__members__user=request.user,community__members__status=CommunityMember.Status.ACTIVE)).exclude(tournament__isnull=False).select_related('sport', 'venue', 'court', 'community', 'organizer').order_by('starts_at')
    now = timezone.now()
    if not date_from and not date_to and lifecycle in (None, '', 'upcoming'):
        qs = qs.filter(starts_at__gte=now)
    if lifecycle == 'upcoming': qs = qs.filter(starts_at__gte=now)
    elif lifecycle == 'ongoing': qs = qs.filter(starts_at__lte=now, ends_at__gte=now)
    elif lifecycle == 'past': qs = qs.filter(ends_at__lt=now)
    if date_from:
        start = timezone.make_aware(datetime.combine(date_from, time.min), timezone.get_current_timezone())
        qs = qs.filter(starts_at__gte=start)
    if date_to:
        end = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min), timezone.get_current_timezone())
        qs = qs.filter(starts_at__lt=end)
    if sport_id:
        if not str(sport_id).isdigit(): raise ValidationError({'sport_id': 'Must be a valid sport id.'})
        qs = qs.filter(sport_id=sport_id)
    if city: qs = qs.filter(venue__city__icontains=city)
    event_type = request.query_params.get('event_type') or request.query_params.get('type')
    if status and status not in Event.Status.values: raise ValidationError({'status':'Invalid event status.'})
    if event_type and event_type not in Event.Type.values: raise ValidationError({'event_type':'Invalid event type.'})
    if event_type: qs = qs.filter(event_type=event_type)
    if status: qs = qs.filter(status=status)
    else: qs = qs.exclude(status=Event.Status.DRAFT)
    if registration_status:
        mine = Q(participants__user=request.user, participants__status=registration_status)
        qs = qs.filter(mine) if registration_status != 'not_registered' else qs.exclude(participants__user=request.user, participants__status__in=[EventParticipant.Status.REGISTERED, EventParticipant.Status.WAITLISTED])
    return qs.distinct()


class EventListCreateView(generics.ListCreateAPIView):
 permission_classes=[IsActiveAccount]; serializer_class=EventSerializer
 def get_queryset(self): return _calendar_filters(self.request)
 def create(self,request,*args,**kwargs):
  s=EventCreateSerializer(data=request.data);s.is_valid(raise_exception=True);d=s.validated_data
  d['venue']=get_object_or_404(Venue,public_id=d.pop('venue_id')); d['court']=get_object_or_404(Court,public_id=d.pop('court_id')) if d.get('court_id') else None; d.pop('court_id',None)
  d['community']=get_object_or_404(Community,public_id=d.pop('community_id')) if d.get('community_id') else None; d.pop('community_id',None); d['game']=get_object_or_404(Game,public_id=d.pop('game_id')) if d.get('game_id') else None; d.pop('game_id',None)
  event=EventService.create(organizer=request.user,**d); return Response({'success':True,'data':EventSerializer(event,context={'request':request}).data},status=201)
class EventDetailView(generics.RetrieveUpdateDestroyAPIView):
 permission_classes=[IsActiveAccount];serializer_class=EventSerializer;lookup_field='public_id'
 def get_queryset(self):
  return Event.objects.filter(Q(visibility=Event.Visibility.PUBLIC)|Q(organizer=self.request.user)|Q(participants__user=self.request.user)|Q(community__members__user=self.request.user,community__members__status=CommunityMember.Status.ACTIVE)).select_related('sport','venue','court','community').distinct()
 @transaction.atomic
 def perform_update(self, serializer):
  event = serializer.instance
  if event.organizer_id != self.request.user.id:
   raise PermissionDenied('Only the event organizer can edit this event.')
  changes = EventUpdateSerializer(event, data=self.request.data, partial=True)
  changes.is_valid(raise_exception=True)
  data = changes.validated_data
  event_date = data.get('event_date', event.event_date)
  start_time = data.get('start_time', event.start_time)
  end_time = data.get('end_time', event.end_time)
  starts_at, ends_at = BookingService._local_datetimes(venue=event.venue, booking_date=event_date, start_time=start_time, end_time=end_time)
  if starts_at <= timezone.now():
   raise ValidationError({'event_date': 'Event must remain in the future.'})
  if data.get('registration_deadline', event.registration_deadline) >= starts_at:
   raise ValidationError({'registration_deadline': 'Deadline must precede the event start.'})
  # Event court usage follows the same authoritative availability rules as
  # bookings. Revalidate only when an event reserves a court and its schedule
  # changes; never silently move an event into a blocked/booked slot.
  schedule_changed = any(key in data for key in ('event_date', 'start_time', 'end_time'))
  if event.court_id and schedule_changed:
   court = Court.objects.select_for_update().get(pk=event.court_id)
   BookingService._validate_request(
    venue=event.venue, court=court, booking_date=event_date,
    start_time=start_time, end_time=end_time, lock_bookings=True,
   )
  for key, value in data.items():
   setattr(event, key, value)
  event.starts_at, event.ends_at = starts_at, ends_at
  event.save(update_fields=[*data.keys(), 'starts_at', 'ends_at', 'updated_at'])
  EventCapacityService.refresh(event)
  from my_app.services.notifications import NotificationService
  for participant in event.participants.filter(status__in=[EventParticipant.Status.REGISTERED, EventParticipant.Status.WAITLISTED]).exclude(user=self.request.user).select_related('user'):
   NotificationService.create_notification(user=participant.user,actor=self.request.user,type='event_updated',title='Event details updated',body=f'The organizer updated {event.title}.',data={'event_id':str(event.public_id)},event_key=f'event-updated:{event.pk}:{event.updated_at.isoformat()}:{participant.user_id}')
 def destroy(self,request,*args,**kwargs):
  e=self.get_object()
  EventCancellationService.cancel(event=e,actor=request.user);return Response(status=204)
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
  e=EventCancellationService.cancel(event=get_object_or_404(Event,public_id=public_id),actor=request.user);return Response({'success':True,'data':EventSerializer(e,context={'request':request}).data})
class EventParticipantsView(generics.ListAPIView):
 permission_classes=[IsActiveAccount];serializer_class=EventParticipantSerializer
 def get_queryset(self):
  event = get_object_or_404(Event.objects.filter(
   Q(visibility=Event.Visibility.PUBLIC) | Q(organizer=self.request.user) |
   Q(participants__user=self.request.user) |
   Q(community__members__user=self.request.user, community__members__status=CommunityMember.Status.ACTIVE)
  ).distinct(), public_id=self.kwargs['public_id'])
  return EventParticipant.objects.filter(event=event, status=EventParticipant.Status.REGISTERED).select_related('user').order_by('joined_at')


class EventReminderView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id):
  minutes=request.data.get('minutes_before',60)
  try: minutes=int(minutes)
  except (TypeError,ValueError) as exc: raise ValidationError({'minutes_before':'Must be an integer.'}) from exc
  reminder=EventReminderService.set_reminder(event=get_object_or_404(Event,public_id=public_id),user=request.user,minutes_before=minutes)
  return Response({'success':True,'data':EventReminderSerializer(reminder).data})
 def delete(self,request,public_id):
  event=get_object_or_404(Event,public_id=public_id)
  reminder=EventReminderService.cancel_reminder(event=event,user=request.user)
  return Response({'success':True,'cancelled':bool(reminder)})


class SportsCalendarView(APIView):
 permission_classes=[IsActiveAccount]
 def get(self,request):
  events=EventSerializer(_calendar_filters(request),many=True,context={'request':request}).data
  tournaments=TournamentSerializer(_calendar_filters(request,tournaments=True),many=True,context={'request':request}).data
  entries=[]
  for item in events:
   entries.append({'kind':'event',**item})
  for item in tournaments:
   entries.append({'kind':'tournament',**item})
  entries.sort(key=lambda item: item.get('starts_at') or item.get('start_date') or '')
  return Response({'success':True,'data':entries})
class EventParticipantRemoveView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id,user_id):
  e=get_object_or_404(Event,public_id=public_id)
  if e.organizer_id!=request.user.id and not request.user.is_staff: raise PermissionDenied('Only organizer can remove participants.')
  return Response({'success':True,'data':EventParticipantSerializer(EventRegistrationService.leave(event=e,user=get_object_or_404(User,pk=user_id),removed=True)).data})
class TournamentListCreateView(generics.ListCreateAPIView):
 permission_classes=[IsActiveAccount];serializer_class=TournamentSerializer
 def get_queryset(self): return _calendar_filters(self.request,tournaments=True)
 def create(self,request,*args,**kwargs):
  s=TournamentCreateSerializer(data=request.data);s.is_valid(raise_exception=True);d=s.validated_data
  if d.get('nearby_ground_id'):
   d['nearby_ground']=get_object_or_404(SportsGround,pk=d.pop('nearby_ground_id'))
  else: d['venue']=get_object_or_404(Venue,public_id=d.pop('venue_id'))
  t=Tournament.objects.create(tournament_reference=EventService.ref('TR'),organizer=request.user,**d);return Response({'success':True,'data':TournamentSerializer(t,context={'request':request}).data},status=201)
class TournamentDetailView(generics.RetrieveUpdateDestroyAPIView):
 permission_classes=[IsActiveAccount];serializer_class=TournamentSerializer;lookup_field='public_id';queryset=Tournament.objects.select_related('sport','venue','nearby_ground')
 @transaction.atomic
 def perform_update(self,serializer):
  tournament = Tournament.objects.select_for_update().get(pk=serializer.instance.pk)
  if tournament.organizer_id!=self.request.user.id: raise PermissionDenied('Only the tournament organizer can edit this tournament.')
  if tournament.status not in (Tournament.Status.OPEN,Tournament.Status.FULL): raise ValidationError('Tournament details cannot be edited after fixtures start.')
  changes = TournamentUpdateSerializer(tournament, data=self.request.data, partial=True)
  changes.is_valid(raise_exception=True)
  for key, value in changes.validated_data.items():
   setattr(tournament, key, value)
  tournament.save(update_fields=[*changes.validated_data.keys(), 'updated_at'])
  serializer.instance=tournament
 def destroy(self,request,*args,**kwargs):
  tournament = self.get_object()
  TournamentService.cancel(tournament=tournament,actor=request.user)
  return Response(status=204)

class TournamentRegistrationView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id):
  serializer=TournamentRegistrationSerializer(data=request.data);serializer.is_valid(raise_exception=True)
  tournament=get_object_or_404(Tournament,public_id=public_id)
  team,created=TournamentRegistrationService.register(tournament=tournament,user=request.user,**serializer.validated_data)
  return Response({'success':True,'created':created,'team':TournamentTeamSerializer(team).data},status=201 if created else 200)
 def delete(self,request,public_id):
  tournament=get_object_or_404(Tournament,public_id=public_id)
  team=TournamentRegistrationService.leave(tournament=tournament,user=request.user)
  return Response({'success':True,'team':TournamentTeamSerializer(team).data})
class TournamentCancelView(APIView):
 permission_classes=[IsActiveAccount]
 def post(self,request,public_id):
  t=get_object_or_404(Tournament,public_id=public_id)
  t=TournamentService.cancel(tournament=t,actor=request.user)
  return Response({'success':True,'data':TournamentSerializer(t,context={'request':request}).data})
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
  serializer=TournamentResultSerializer(data=request.data);serializer.is_valid(raise_exception=True)
  m=get_object_or_404(TournamentMatch,tournament__public_id=public_id,public_id=match_id)
  m=TournamentMatchService.result(match=m,actor=request.user,**serializer.validated_data)
  return Response({'success':True,'data':TournamentMatchSerializer(m).data})
class TournamentStandingsView(APIView):
 permission_classes=[IsActiveAccount]
 def get(self,request,public_id):
  return Response({'success':True,'data':[{'team':r['team'].name,'played':r['played'],'wins':r['wins'],'losses':r['losses'],'draws':r['draws'],'points':r['points'],'difference':r['difference']} for r in TournamentStandingsService.standings(get_object_or_404(Tournament,public_id=public_id))]})


class CollegeListCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        qs = College.objects.all().order_by('name')
        status = request.query_params.get('status')
        own = request.query_params.get('mine') == 'true'
        if status and status not in College.VerificationStatus.values:
            raise ValidationError({'status': 'Choose pending, verified, or rejected.'})
        if own:
            # A submitter must retain access to their application status even
            # before approval/rejection; active and former members can also
            # find their associated college from this personal list.
            qs = qs.filter(Q(submitted_by=request.user) | Q(memberships__user=request.user)).distinct()
            if status:
                qs = qs.filter(verification_status=status)
        elif status == College.VerificationStatus.PENDING:
            if not has_platform_role(request.user, ADMIN_GROUPS):
                raise PermissionDenied('Only platform administrators may list pending college submissions.')
            qs = qs.filter(verification_status=College.VerificationStatus.PENDING)
        elif status == College.VerificationStatus.REJECTED:
            raise PermissionDenied('Rejected college submissions are visible only to their submitter.')
        else:
            # Public discovery deliberately includes only verified colleges.
            qs = qs.filter(verification_status=College.VerificationStatus.VERIFIED)
        return Response({'success': True, 'data': CollegeSerializer(qs, many=True, context={'request': request}).data})

    def post(self, request):
        serializer = CollegeSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        college = CollegeService.submit(actor=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': CollegeSerializer(college).data}, status=201)


class CollegeDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        college = get_object_or_404(College, public_id=public_id)
        if college.verification_status != College.VerificationStatus.VERIFIED and not (college.submitted_by_id == request.user.id or has_platform_role(request.user, ADMIN_GROUPS)):
            from rest_framework.exceptions import NotFound
            raise NotFound('College not found.')
        return Response({'success': True, 'data': CollegeSerializer(college, context={'request': request}).data})




class CollegeMembershipView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        college = get_object_or_404(College, public_id=public_id)
        admin = CollegeMembership.objects.filter(college=college, user=request.user, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.ACTIVE).exists()
        if not admin and not has_platform_role(request.user, ADMIN_GROUPS):
            raise PermissionDenied('Only college administrators may view the membership roster.')
        status = CollegeMembership.Status.PENDING if request.query_params.get('status') == 'pending' else CollegeMembership.Status.ACTIVE
        return Response({'success': True, 'data': CollegeMembershipSerializer(college.memberships.filter(status=status).select_related('user'), many=True).data})

    def post(self, request, public_id):
        college = get_object_or_404(College, public_id=public_id)
        member, changed = CollegeService.request_membership(college=college, user=request.user)
        return Response({'success': True, 'changed': changed, 'data': CollegeMembershipSerializer(member).data}, status=201 if changed else 200)


class CollegeMembershipDecisionView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id, user_id, approve):
        if approve not in ('approve', 'reject'):
            raise ValidationError({'decision': 'Use approve or reject.'})
        college = get_object_or_404(College, public_id=public_id)
        member = CollegeService.decide_membership(college=college, actor=request.user, user_id=user_id, approve=approve == 'approve')
        return Response({'success': True, 'data': CollegeMembershipSerializer(member).data})


class CollegeClubListCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        college = get_object_or_404(College, public_id=public_id, verification_status=College.VerificationStatus.VERIFIED)
        qs = Community.objects.filter(college=college, is_club=True, is_active=True).select_related('sport', 'owner')
        return Response({'success': True, 'data': CommunitySerializer(qs, many=True, context={'request': request}).data})

    def post(self, request, public_id):
        college = get_object_or_404(College, public_id=public_id)
        name = (request.data.get('name') or '').strip()
        if not name:
            raise ValidationError({'name': 'Required.'})
        sport = get_object_or_404(Sport, pk=request.data.get('sport_id'), is_active=True)
        community = CollegeService.create_club(college=college, actor=request.user, name=name, sport=sport, description=request.data.get('description', ''), visibility=request.data.get('visibility', 'public'))
        return Response({'success': True, 'data': CommunitySerializer(community, context={'request': request}).data}, status=201)


class CollegeChallengeListCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        memberships = CollegeMembership.objects.filter(user=request.user, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.ACTIVE).values('college_id')
        qs = CollegeChallenge.objects.filter(Q(challenger_id__in=memberships) | Q(challenged_id__in=memberships)).select_related('challenger', 'challenged', 'sport', 'tournament').order_by('-created_at')
        return Response({'success': True, 'data': CollegeChallengeSerializer(qs, many=True).data})

    def post(self, request, public_id):
        challenger = get_object_or_404(College, public_id=public_id)
        serializer = CollegeChallengeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        challenged = get_object_or_404(College, public_id=serializer.validated_data['challenged_college_id'])
        data = serializer.validated_data
        challenge = CollegeService.create_challenge(challenger=challenger, challenged=challenged, actor=request.user, sport=data['sport'], message=data['message'])
        return Response({'success': True, 'data': CollegeChallengeSerializer(challenge).data}, status=201)


class CollegeChallengeAnswerView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        challenge = get_object_or_404(CollegeChallenge, public_id=public_id)
        serializer = ChallengeAnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        args = serializer.validated_data
        venue = None
        if args.get('accept'):
            for required in ('venue_id', 'start_date', 'end_date', 'registration_deadline'):
                if required not in args:
                    raise ValidationError({required: 'Required when accepting a challenge.'})
            venue = get_object_or_404(Venue, public_id=args.pop('venue_id'), active=True)
        challenge = CollegeService.answer_challenge(challenge=challenge, actor=request.user, venue=venue, **args)
        return Response({'success': True, 'data': CollegeChallengeSerializer(challenge).data})


class CollegeTeamsView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        tournament = get_object_or_404(Tournament, public_id=public_id, is_inter_college=True)
        serializer = CollegeTeamCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        college = get_object_or_404(College, public_id=serializer.validated_data.pop('college_id'))
        team = CollegeService.create_team(tournament=tournament, college=college, actor=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': TournamentTeamSerializer(team).data}, status=201)

    def get(self, request, public_id):
        tournament = get_object_or_404(Tournament, public_id=public_id, is_inter_college=True)
        teams = tournament.teams.select_related('college', 'captain').all()
        data = TournamentTeamSerializer(teams, many=True).data
        for row, team in zip(data, teams):
            row['college_name'] = team.college.name if team.college_id else None
            row['college_public_id'] = str(team.college.public_id) if team.college_id else None
        return Response({'success': True, 'data': data})


class CollegeRosterView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        team = get_object_or_404(
            TournamentTeam.objects.select_related('college', 'tournament'),
            public_id=public_id, tournament__is_inter_college=True,
        )
        allowed = (
            has_platform_role(request.user, ADMIN_GROUPS)
            or team.tournament.organizer_id == request.user.id
            or CollegeMembership.objects.filter(
                college=team.college, user=request.user,
                role=CollegeMembership.Role.ADMIN,
                status=CollegeMembership.Status.ACTIVE,
            ).exists()
            or TournamentTeamMember.objects.filter(
                team=team, user=request.user,
                status=TournamentTeamMember.Status.ACTIVE,
            ).exists()
        )
        if not allowed:
            raise PermissionDenied('Only the tournament organizer, college administrators, and team players may view this roster.')
        members = team.members.filter(
            status=TournamentTeamMember.Status.ACTIVE,
        ).select_related('user').order_by('joined_at')
        return Response({'success': True, 'data': CollegeTeamMemberSerializer(members, many=True).data})

    def post(self, request, public_id):
        team = get_object_or_404(TournamentTeam, public_id=public_id, tournament__is_inter_college=True)
        serializer = CollegeRosterAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = get_object_or_404(User, pk=serializer.validated_data['user_id'], is_active=True)
        member = CollegeService.add_roster_member(team=team, actor=request.user, user=user)
        return Response({'success': True, 'data': {'user_id': member.user_id, 'team_id': team.public_id.hex}}, status=201)


class CollegeResultDisputeView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        match = get_object_or_404(TournamentMatch, public_id=public_id, tournament__is_inter_college=True)
        if not has_platform_role(request.user, ADMIN_GROUPS):
            rostered = TournamentTeamMember.objects.filter(
                team__in=[match.team_a, match.team_b], user=request.user,
                status=TournamentTeamMember.Status.ACTIVE,
            ).exists()
            college_admin = CollegeMembership.objects.filter(
                user=request.user, role=CollegeMembership.Role.ADMIN,
                status=CollegeMembership.Status.ACTIVE,
                college__in=[team.college for team in (match.team_a, match.team_b) if team],
            ).exists()
            if not rostered and not college_admin:
                raise PermissionDenied('Only participating players and college administrators may view this dispute history.')
        return Response({'success': True, 'data': CollegeResultDisputeSerializer(match.college_disputes.all(), many=True).data})

    def post(self, request, public_id):
        match = get_object_or_404(TournamentMatch, public_id=public_id, tournament__is_inter_college=True)
        serializer = CollegeDisputeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dispute = CollegeService.dispute_result(match=match, actor=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': CollegeResultDisputeSerializer(dispute).data}, status=201)






class CollegeTournamentResultView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id, match_id):
        match = get_object_or_404(TournamentMatch, public_id=match_id, tournament__public_id=public_id, tournament__is_inter_college=True)
        updated = CollegeService.record_result(match=match, actor=request.user, score_a=request.data.get('score_a'), score_b=request.data.get('score_b'))
        return Response({'success': True, 'data': TournamentMatchSerializer(updated).data})


class CollegeTournamentBracketView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        tournament = get_object_or_404(Tournament, public_id=public_id, is_inter_college=True)
        # Any participating college administrator may create the fixture once
        # both sides have registered eligible rosters. The generic organizer
        # endpoint retains its original permission rule for other tournaments.
        from .models import CollegeChallenge, CollegeMembership, TournamentTeamMember
        challenge = getattr(tournament, 'college_challenge', None)
        if not challenge or challenge.status != CollegeChallenge.Status.ACCEPTED:
            raise ValidationError('An accepted inter-college challenge is required.')
        if not CollegeMembership.objects.filter(user=request.user, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.ACTIVE, college__in=[challenge.challenger, challenge.challenged], college__verification_status=College.VerificationStatus.VERIFIED).exists():
            raise PermissionDenied('Participating college administrator permission is required.')
        teams = list(tournament.teams.filter(status=TournamentTeam.Status.ACTIVE).select_related('college').order_by('seed', 'created_at'))
        if len(teams) != 2 or {team.college_id for team in teams} != {challenge.challenger_id, challenge.challenged_id}:
            raise ValidationError('Both colleges must register exactly one eligible team before fixtures are created.')
        for team in teams:
            if not team.members.filter(status=TournamentTeamMember.Status.ACTIVE).exists() or team.members.filter(status=TournamentTeamMember.Status.ACTIVE).exclude(user__college_memberships__college=team.college, user__college_memberships__status=CollegeMembership.Status.ACTIVE).exists():
                raise ValidationError('Every rostered player must have active membership at their represented college.')
        matches = TournamentBracketService.generate(tournament=tournament, actor=tournament.organizer)
        return Response({'success': True, 'data': TournamentMatchSerializer(matches, many=True).data})


# ---------------- COMMUNITIES ----------------
class CommunityListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsActiveAccount]

    def get_serializer_class(self):
        return CommunityCreateSerializer if self.request.method == 'POST' else CommunitySerializer

    def get_queryset(self):
        query = CommunityDiscoveryQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        filters = query.validated_data
        visible = (Q(visibility=Community.Visibility.PUBLIC, is_club=False)
                   | Q(owner=self.request.user)
                   | Q(members__user=self.request.user, members__status=CommunityMember.Status.ACTIVE)
                   | Q(visibility=Community.Visibility.PUBLIC, is_club=True))
        queryset = Community.objects.filter(visible, is_active=True).select_related('sport', 'owner').distinct()
        if filters.get('communities'):
            queryset = queryset.filter(is_club=False)
        if filters.get('clubs'):
            queryset = queryset.filter(is_club=True)
            queryset = queryset.filter(
                Q(visibility=Community.Visibility.PUBLIC)
                | Q(members__user=self.request.user, members__status=CommunityMember.Status.ACTIVE)
            )
        sport = filters.get('sport')
        if sport:
            queryset = queryset.filter(sport=sport)
        city = filters.get('city', '').strip()
        if city:
            queryset = queryset.filter(city__iexact=city)
        latitude = filters.get('latitude', self.request.user.latitude)
        longitude = filters.get('longitude', self.request.user.longitude)
        radius = filters.get('radius', 20)
        if latitude is not None and longitude is not None:
            ranked = CommunityDiscoveryService.discover(user=self.request.user, queryset=queryset, latitude=latitude, longitude=longitude, radius=radius)
            return ranked
        return CommunityDiscoveryService.discover(user=self.request.user, queryset=queryset)

    def create(self, request, *args, **kwargs):
        serializer = CommunityCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        community = CommunityService.create(owner=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': CommunitySerializer(community, context={'request': request}).data}, status=201)


class CommunityDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get_community(self, public_id):
        community = get_object_or_404(Community, public_id=public_id, is_active=True)
        # Public club details must be readable before joining, otherwise the
        # discovery flow cannot reach its join action. Roster/content remain
        # member-gated by their respective endpoints.
        if community.visibility == Community.Visibility.PRIVATE:
            CommunityMembershipService.active_member(community=community, user=self.request.user)
        return community

    def get(self, request, public_id):
        return Response({'success': True, 'data': CommunitySerializer(self.get_community(public_id), context={'request': request}).data})

    def patch(self, request, public_id):
        community = self.get_community(public_id)
        CommunityMembershipService.admin(community=community, user=request.user)
        serializer = CommunityUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if 'is_active' in serializer.validated_data and request.user.id != community.owner_id:
            raise PermissionDenied('Only the owner can deactivate this club.')
        if request.user.id != community.owner_id and {'visibility', 'membership_open', 'avatar', 'cover_image'} & set(serializer.validated_data):
            raise PermissionDenied('Only the owner can change club visibility, membership rules, or branding.')
        for key, value in serializer.validated_data.items():
            setattr(community, key, value)
        community.save(update_fields=[*serializer.validated_data.keys(), 'updated_at'])
        return Response({'success': True, 'data': CommunitySerializer(community, context={'request': request}).data})

    def delete(self, request, public_id):
        community = get_object_or_404(Community, public_id=public_id, is_active=True)
        if community.owner_id != request.user.id:
            raise PermissionDenied('Only the community owner can archive it.')
        community.is_active = False
        community.save(update_fields=['is_active', 'updated_at'])
        return Response(status=204)


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
        if community.visibility == Community.Visibility.PRIVATE or community.is_club:
            CommunityMembershipService.active_member(community=community, user=self.request.user)
        return CommunityMember.objects.filter(community=community, status=CommunityMember.Status.ACTIVE).select_related('user').order_by('joined_at')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        community = get_object_or_404(Community, public_id=self.kwargs['public_id'])
        own = CommunityMember.objects.filter(community=community, user=request.user, status=CommunityMember.Status.ACTIVE).exists()
        return Response({'success': True, 'data': response.data, 'can_manage': own and CommunityMember.objects.filter(community=community, user=request.user, status=CommunityMember.Status.ACTIVE, role__in=[CommunityMember.Role.OWNER, CommunityMember.Role.CAPTAIN, CommunityMember.Role.ADMIN]).exists()})


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


class ClubInvitationsView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        community = get_object_or_404(Community, public_id=public_id, is_active=True, is_club=True)
        serializer = ClubInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = CommunityService.invite(community=community, actor=request.user, user_id=serializer.validated_data['user_id'])
        return Response({'success': True, 'data': CommunityMemberSerializer(member).data}, status=201)


class ClubMemberRoleView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request, public_id, user_id):
        community = get_object_or_404(Community, public_id=public_id, is_active=True, is_club=True)
        serializer = ClubMemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = CommunityService.set_role(community=community, actor=request.user, user_id=user_id, role=serializer.validated_data['role'])
        return Response({'success': True, 'data': CommunityMemberSerializer(member).data})


class ClubOwnershipTransferView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        community = get_object_or_404(Community, public_id=public_id, is_active=True, is_club=True)
        serializer = ClubInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = CommunityService.transfer_ownership(community=community, actor=request.user, user_id=serializer.validated_data['user_id'])
        return Response({'success': True, 'data': CommunityMemberSerializer(member).data})


class ClubActivitiesView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        community = get_object_or_404(Community, public_id=public_id, is_active=True, is_club=True)
        if community.visibility == Community.Visibility.PRIVATE or community.is_club:
            CommunityMembershipService.active_member(community=community, user=request.user)
        events = community.events.filter(status__in=[Event.Status.OPEN, Event.Status.FULL, Event.Status.STARTED]).filter(
            Q(visibility__in=[Event.Visibility.PUBLIC, Event.Visibility.COMMUNITY]) | Q(organizer=request.user),
        ).select_related('sport', 'venue').order_by('starts_at')
        games = community.posts.filter(post_type=CommunityPost.Type.GAME, game__starts_at__gt=timezone.now()).filter(
            Q(game__visibility=Game.Visibility.PUBLIC) | Q(game__host=request.user)
            | Q(game__game_players__user=request.user, game__game_players__status=GamePlayer.Status.CONFIRMED),
        ).select_related('game__sport', 'game__venue').distinct().order_by('game__starts_at')
        return Response({'success': True, 'events': EventSerializer(events, many=True).data,
            'games': [{'post': CommunityPostSerializer(post).data, 'game': GameSerializer(post.game, context={'request': request}).data} for post in games if post.game_id]})


class CommunityPostsView(generics.ListCreateAPIView):
    serializer_class = CommunityPostSerializer
    permission_classes = [IsActiveAccount]

    def get_community(self):
        community = get_object_or_404(Community, public_id=self.kwargs['public_id'], is_active=True)
        if community.visibility == Community.Visibility.PRIVATE or community.is_club:
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
        if post.community.visibility == Community.Visibility.PRIVATE or post.community.is_club:
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
        if post.community.visibility == Community.Visibility.PRIVATE or post.community.is_club:
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
        for message in Message.objects.filter(conversation_id__in=conversation_ids).select_related('sender').order_by('conversation_id', '-created_at', '-pk'):
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
        latest = Message.objects.filter(conversation=conversation).select_related('sender').order_by('-created_at', '-pk').first()
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
        return Message.objects.filter(conversation=conversation).select_related('sender').order_by('-created_at', '-public_id')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        before = request.query_params.get('before')
        if before:
            try:
                cursor_id = UUID(before)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError({'before': ['A valid message cursor is required.']}) from exc
            cursor = queryset.filter(public_id=cursor_id).first()
            if cursor is None:
                raise ValidationError({'before': ['The message cursor is not in this conversation.']})
            queryset = queryset.filter(
                Q(created_at__lt=cursor.created_at)
                | Q(created_at=cursor.created_at, public_id__lt=cursor.public_id)
            )
        try:
            page_size = int(request.query_params.get('page_size', 50))
        except (TypeError, ValueError) as exc:
            raise ValidationError({'page_size': ['page_size must be an integer.']}) from exc
        if not 1 <= page_size <= 100:
            raise ValidationError({'page_size': ['page_size must be between 1 and 100.']})
        rows = list(queryset[:page_size + 1])
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        return Response({
            'success': True,
            'results': ConversationMessageSerializer(rows, many=True).data,
            'next_cursor': str(rows[-1].public_id) if has_more and rows else None,
        })

    def create(self, request, *args, **kwargs):
        serializer = ConversationMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message = MessageService.send(
            conversation=self.get_conversation(), sender=request.user,
            content=serializer.validated_data['content'],
            client_message_id=serializer.validated_data.get('client_message_id'),
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
        # Staff status is not authorization to inspect private conversations.
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

        if other == me or not User.objects.filter(pk=other, is_active=True, account_status=User.AccountStatus.ACTIVE).exists():
            return Message.objects.none()

        messages = Message.objects.filter(
            Q(sender_id=me, receiver_id=other) |
            Q(sender_id=other, receiver_id=me)
        ).order_by('created_at', 'id')

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
        serializer = LegacyMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        receiver_id = serializer.validated_data['receiver']
        content = serializer.validated_data['content'].strip()
        if not content:
            raise ValidationError({'content': ['Message content cannot be empty.']})
        receiver = get_object_or_404(
            User, pk=receiver_id, is_active=True,
            account_status=User.AccountStatus.ACTIVE,
        )
        if receiver.pk == request.user.pk:
            raise ValidationError({'receiver': ['You cannot message yourself.']})
        ChatPermissionService.ensure_not_blocked(user=request.user, other_user=receiver)

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
        # Build one indexed database count rather than loading every message
        # into Python. Large inboxes previously made the chat tab exceed the
        # mobile client's request timeout.
        unread_filter = Q()
        for conversation_id, last_read_at in last_read_by_conversation.items():
            item_filter = Q(conversation_id=conversation_id)
            if last_read_at is not None:
                item_filter &= Q(created_at__gt=last_read_at)
            unread_filter |= item_filter
        conversation_count = (
            Message.objects.filter(unread_filter, deleted_at__isnull=True)
            .exclude(sender=request.user)
            .count()
            if last_read_by_conversation else 0
        )
        return Response({'unread': legacy_count + conversation_count})

# ---------------- NOTIFICATIONS ----------------
class GlobalSearchView(APIView):
    permission_classes=[IsActiveAccount]
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='search'
    def get(self,request):
        q=(request.query_params.get('q') or '').strip()
        raw_types=request.query_params.get('type','').split(',') if request.query_params.get('type') else None
        if raw_types and not set(raw_types)<=VALID_TYPES: raise ValidationError({'type':['Unsupported search type.']})
        # A category may be browsed without a keyword (for example, all
        # available players).  An unfiltered "All" request remains invalid,
        # since it would unnecessarily query every public resource.
        if not q and not raw_types:
            raise ValidationError({'q':['Enter a search query or choose a category.']})
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
        user = self.request.user
        if self.kwargs.get('public_id') is not None:
            blocked_ids = UserBlock.objects.filter(user=self.request.user).values('blocked_user_id')
            blocked_by_ids = UserBlock.objects.filter(blocked_user=self.request.user).values('user_id')
            visible_users = User.objects.filter(
                is_active=True, account_status=User.AccountStatus.ACTIVE,
            ).filter(Q(profile__profile_visibility='public') | Q(pk=self.request.user.pk))
            user = get_object_or_404(
                visible_users.exclude(pk__in=blocked_ids).exclude(pk__in=blocked_by_ids),
                public_id=self.kwargs['public_id'],
            )
        if user.pk == self.request.user.pk:
            AchievementEvaluationService.evaluate(user=user)
        return UserAchievement.objects.filter(user=user).select_related('achievement').order_by('achievement__category','achievement__name')


class ChallengeListView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        query = ChallengeListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        challenges = SportChallenge.objects.select_related('sport', 'reward_achievement').order_by('starts_on', 'name')
        if 'sport_id' in params:
            challenges = challenges.filter(sport_id=params['sport_id'])
        if 'kind' in params:
            challenges = challenges.filter(kind=params['kind'])
        today = timezone.localdate()
        status = params.get('status')
        if status == 'upcoming':
            challenges = challenges.filter(is_active=True, starts_on__gt=today)
        elif status == 'active':
            challenges = challenges.filter(is_active=True, starts_on__lte=today, ends_on__gte=today)
        elif status == 'completed':
            challenges = challenges.filter(is_active=True, ends_on__lt=today)
        elif status == 'cancelled':
            challenges = challenges.filter(is_active=False)
        return Response({'success': True, 'data': [
            ChallengeService.serialize_challenge(item, viewer=request.user) for item in challenges
        ]})


class ChallengeDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request, public_id):
        challenge = get_object_or_404(
            SportChallenge.objects.select_related('sport', 'reward_achievement'), public_id=public_id,
        )
        return Response({'success': True, 'data': ChallengeService.serialize_challenge(challenge, viewer=request.user)})


class ChallengeJoinView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        challenge = get_object_or_404(SportChallenge, public_id=public_id)
        serializer = ChallengeJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = ChallengeService.join(
            challenge=challenge, user=request.user,
            team_id=serializer.validated_data.get('team_id'),
        )
        return Response({'success': True, 'data': ChallengeService.serialize_entry(entry, viewer=request.user)}, status=201)


class ChallengeLeaveView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        entry = get_object_or_404(ChallengeEntry.objects.select_related('challenge', 'team', 'user'), public_id=public_id)
        entry = ChallengeService.leave(entry=entry, user=request.user)
        return Response({'success': True, 'data': ChallengeService.serialize_entry(entry, viewer=request.user)})
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



















class PlayerRatingsView(generics.ListCreateAPIView):
    permission_classes=[IsActiveAccount];serializer_class=PlayerRatingSerializer
    throttle_classes=[ScopedRateThrottle]
    throttle_scope='ratings'
    def get_player(self): return get_object_or_404(User,public_id=self.kwargs['public_id'],is_active=True)
    def get_queryset(self): return PlayerRating.objects.filter(player=self.get_player(),deleted_at__isnull=True).select_related('reviewer','game').order_by('-created_at')
    def create(self,request,*args,**kwargs):
        s=PlayerRatingCreateSerializer(data=request.data);s.is_valid(raise_exception=True);values=s.validated_data.copy();game=get_object_or_404(Game,public_id=values.pop('game_id'));rating=RatingService.rate_player(reviewer=request.user,player=self.get_player(),game=game,**values);return Response({'success':True,'data':PlayerRatingSerializer(rating).data},status=201)

class PlayerRatingSummaryView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request,public_id):
        return Response({'success':True,'data':RatingService.aggregate(get_object_or_404(User,public_id=public_id,is_active=True))})

class PlayerRatingEligibilityView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request,public_id):
        player=get_object_or_404(User,public_id=public_id,is_active=True)
        games=RatingService.eligible_games(reviewer=request.user,player=player).select_related('sport','venue','court')
        # A soft-deleted submission still consumes the one submission allowed
        # per reviewer/player/game; deletion removes it from aggregates only.
        rated_game_ids=set(PlayerRating.objects.filter(reviewer=request.user,player=player).values_list('game_id',flat=True))
        return Response({'success':True,'data':[{'game_id':str(game.public_id),'game_reference':game.game_reference,'sport':game.sport.name,'date':game.game_date,'already_rated':game.id in rated_game_ids} for game in games]})

class GameRatingStatusView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request,public_id):
        game=get_object_or_404(Game,public_id=public_id)
        if game.status != Game.Status.COMPLETED or not GamePlayer.objects.filter(game=game,user=request.user,status=GamePlayer.Status.CONFIRMED).exists():
            raise PermissionDenied('Ratings are available only to participants of completed games.')
        all_ratings=PlayerRating.objects.filter(game=game,reviewer=request.user)
        rated_player_ids=set(all_ratings.values_list('player_id',flat=True))
        ratings={rating.player_id:rating.public_id for rating in all_ratings.filter(deleted_at__isnull=True)}
        participants=GamePlayer.objects.filter(game=game,status=GamePlayer.Status.CONFIRMED).exclude(user=request.user).select_related('user')
        return Response({'success':True,'data':[{'player_id':str(row.user.public_id),'username':row.user.username,'rating_id':str(ratings[row.user_id]) if row.user_id in ratings else None,'can_rate':row.user_id not in rated_player_ids} for row in participants]})


class MyMatchReflectionsView(APIView):
    """Private reflection history and personal-only trend summary."""
    permission_classes = [IsActiveAccount]

    def get(self, request):
        reflections = MatchReflection.objects.filter(user=request.user).select_related('game__sport', 'game__venue').order_by('-created_at')
        rows = list(reflections)
        count = len(rows)
        averages = {
            key: round(sum(getattr(item, key) for item in rows) / count, 2) if count else None
            for key in ('enjoyment', 'teamwork', 'improvement')
        }
        return Response({'success': True, 'data': {
            'summary': {'count': count, 'averages': averages},
            'results': MatchReflectionSerializer(rows, many=True).data,
        }})


class EligibleMatchReflectionsView(APIView):
    permission_classes = [IsActiveAccount]

    def get(self, request):
        completed = Game.objects.filter(
            status=Game.Status.COMPLETED,
            game_players__user=request.user,
            game_players__status=GamePlayer.Status.CONFIRMED,
        ).exclude(reflections__user=request.user).select_related('sport', 'venue').distinct().order_by('-game_date')
        return Response({'success': True, 'data': [
            {'game_id': str(game.public_id), 'game_reference': game.game_reference,
             'sport': game.sport.name, 'game_date': game.game_date, 'venue': game.venue.name}
            for game in completed
        ]})


class MatchReflectionCreateView(APIView):
    permission_classes = [IsActiveAccount]

    def post(self, request, public_id):
        game = get_object_or_404(Game, public_id=public_id)
        if game.status != Game.Status.COMPLETED or not GamePlayer.objects.filter(
            game=game, user=request.user, status=GamePlayer.Status.CONFIRMED
        ).exists():
            raise PermissionDenied('Reflections are available only to confirmed participants of completed games.')
        serializer = MatchReflectionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                reflection = MatchReflection.objects.create(game=game, user=request.user, **serializer.validated_data)
        except IntegrityError as exc:
            raise ValidationError({'detail': 'You have already reflected on this game.'}) from exc
        return Response({'success': True, 'data': MatchReflectionSerializer(reflection).data}, status=201)


class MatchReflectionDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def patch(self, request, public_id):
        reflection = get_object_or_404(MatchReflection, public_id=public_id)
        if reflection.user_id != request.user.id:
            raise PermissionDenied('Only the reflection owner can edit it.')
        if timezone.now() > reflection.created_at + timedelta(hours=24):
            raise ValidationError({'detail': 'Reflections can only be edited within 24 hours of submission.'})
        serializer = MatchReflectionInputSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.validated_data.items():
            setattr(reflection, key, value)
        reflection.save(update_fields=[*serializer.validated_data.keys(), 'updated_at'])
        return Response({'success': True, 'data': MatchReflectionSerializer(reflection).data})


class PlayerStatisticsView(APIView):
    permission_classes=[IsActiveAccount]
    def get(self,request,public_id,sport_id=None):
        user=get_object_or_404(User,public_id=public_id,is_active=True);sport=get_object_or_404(Sport,pk=sport_id) if sport_id else None
        return Response({'success':True,'data':PlayerStatisticsService.statistics(user=user,sport=sport) if not sport else PlayerStatisticsService.sport_statistics(user=user,sport=sport)})


class GameHistoryView(generics.ListAPIView):
    """Private history of court-backed games the authenticated player joined."""
    permission_classes = [IsActiveAccount]
    serializer_class = GameSerializer

    def get_queryset(self):
        return Game.objects.filter(game_players__user=self.request.user).select_related(
            'sport', 'venue', 'court__sport', 'host',
        ).prefetch_related('game_players__user__user_sports__sport').distinct().order_by('-ends_at')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        for game in queryset[:100]:
            GameLifecycleService.refresh(game)
        return super().list(request, *args, **kwargs)
class RatingDetailView(APIView):
    permission_classes=[IsActiveAccount]
    def patch(self,request,public_id):
        rating=get_object_or_404(PlayerRating,public_id=public_id,deleted_at__isnull=True)
        if rating.reviewer_id!=request.user.id: raise PermissionDenied('Only reviewer can edit.')
        s=PlayerRatingCreateSerializer(data={**request.data,'game_id':str(rating.game.public_id)});s.is_valid(raise_exception=True)
        values=s.validated_data;rating.playing_performance=values['playing_performance'];rating.teamwork=values['teamwork'];rating.sportsmanship=values['sportsmanship'];rating.punctuality=values['punctuality'];rating.rating=RatingService.calculate_overall(playing_performance=rating.playing_performance,teamwork=rating.teamwork,sportsmanship=rating.sportsmanship,punctuality=rating.punctuality);rating.review=values.get('review','').strip();rating.save(update_fields=['rating','playing_performance','teamwork','sportsmanship','punctuality','review','updated_at']);return Response({'success':True,'data':PlayerRatingSerializer(rating).data})
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
        from django.utils.dateparse import parse_datetime
        from rest_framework.exceptions import ValidationError
        is_read=self.request.query_params.get('is_read')
        if is_read is not None:
            if is_read not in ('true','false'): raise ValidationError({'is_read':['Use true or false.']})
            queryset=queryset.filter(is_read=is_read=='true')
        notification_type=self.request.query_params.get('notification_type')
        if notification_type:
            if len(notification_type)>20: raise ValidationError({'notification_type':['Must be at most 20 characters.']})
            queryset=queryset.filter(type=notification_type)
        for parameter, lookup in (('created_after','created_at__gte'),('created_before','created_at__lte')):
            raw=self.request.query_params.get(parameter)
            if raw:
                value=parse_datetime(raw)
                if value is None: raise ValidationError({parameter:['Enter a valid ISO-8601 date-time.']})
                queryset=queryset.filter(**{lookup:value})
        return queryset

class NotificationDetailView(generics.RetrieveDestroyAPIView):
    permission_classes=[IsActiveAccount];serializer_class=NotificationSerializer;lookup_field='public_id'
    def get_queryset(self): return Notification.objects.filter(user=self.request.user,deleted_at__isnull=True)
    def perform_destroy(self,instance): NotificationService.delete_notification(notification=instance,user=self.request.user)
class NotificationReadView(APIView):
    permission_classes=[IsActiveAccount]
    def post(self,request,public_id):
        n=get_object_or_404(Notification,user=request.user,public_id=public_id,deleted_at__isnull=True);return Response({'success':True,'data':NotificationSerializer(NotificationService.mark_as_read(notification=n,user=request.user)).data})
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
    def get(self,request): return Response({'success':True,'delivery_status':'not_configured','data':UserDeviceSerializer(UserDevice.objects.filter(user=request.user,is_active=True),many=True).data})
    def post(self,request):
        s=UserDeviceCreateSerializer(data=request.data);s.is_valid(raise_exception=True);device,created=DeviceService.register(user=request.user,**s.validated_data);return Response({'success':True,'created':created,'delivery_status':'not_configured','data':UserDeviceSerializer(device).data},status=201 if created else 200)
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
    permission_classes = [IsActiveAccount]

    def get(self, request):
        user = request.user
        # This client consumes court-backed games, not legacy ground-based
        # Match rows. Use the shared matcher so capacity, privacy, blocks,
        # preferences and upcoming-state rules stay consistent with the
        # recommendations feed rather than returning full/started games.
        results = GameMatchingService.get_recommendations(
            user=user,
            latitude=user.latitude if user.latitude is not None and user.longitude is not None else None,
            longitude=user.longitude if user.latitude is not None and user.longitude is not None else None,
            radius=getattr(getattr(user, 'profile', None), 'preferred_distance', 15) or 15,
            limit=10,
        )
        games = [item['game'] for item in results]
        if request.query_params.get('include_owned', '').lower() in {'1', 'true', 'yes'}:
            owned = list(Game.objects.filter(
                host=user,
                status__in=[Game.Status.OPEN, Game.Status.ALMOST_FULL],
                ends_at__gt=timezone.now(),
            ).select_related('sport', 'venue', 'court__sport', 'host').order_by('starts_at')[:10])
            owned_ids = {game.pk for game in owned}
            games = owned + [game for game in games if game.pk not in owned_ids]
        response = GameSerializer(
            games[:10], many=True,
            context={'request': request},
        ).data
        result_by_id = {item['game'].pk: item for item in results}
        for serialized, game in zip(response, games[:10]):
            item = result_by_id.get(game.pk)
            serialized['match_score'] = item['match_score'] if item else 100
            serialized['reasons'] = item['reasons'] if item else ['Created from your preferences']
        return Response(response)

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
        if 'latitude' in data:
            request.user.latitude = data['latitude']
            request.user.longitude = data['longitude']
            request.user.save(update_fields=['latitude', 'longitude'])
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


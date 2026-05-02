from django.contrib.auth import get_user_model, authenticate
from django.db.models import Q
from rest_framework import generics
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail

from .models import (
    Sport, AvailabilitySlot, Match, Message,
    Notification, Ground
)

from .serializers import (
    RegisterSerializer, UserSerializer, SportSerializer,
    MatchSerializer, MessageSerializer,
    NotificationSerializer, AvailabilityToggleSerializer,
    GroundSerializer
)

User = get_user_model()


# ---------------- TOKEN ----------------
def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {
        'access': str(refresh.access_token),
        'refresh': str(refresh)
    }


# ---------------- AUTH ----------------
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        user = self.get_serializer(data=request.data)
        user.is_valid(raise_exception=True)
        user = user.save()

        return Response({
            'user': UserSerializer(user).data,
            'tokens': tokens_for(user),
        })


@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    email = request.data.get('email')
    password = request.data.get('password')

    user = authenticate(request, username=email, password=password)

    if not user:
        return Response({'detail': 'Invalid credentials'}, status=401)

    # ✅ mark user online
    user.is_online = True
    user.save()

    return Response({
        'user': UserSerializer(user).data,
        'tokens': tokens_for(user),
    })


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.user.is_online = False
        request.user.save()
        return Response({"msg": "Logged out"})
    
# ---------------- FORGOT PASSWORD ----------------
@api_view(['POST'])
@permission_classes([AllowAny])
def forgot_password(request):
    email = request.data.get('email')

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=404)

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = PasswordResetTokenGenerator().make_token(user)

    reset_link = f"http://localhost:3000/reset-password/{uid}/{token}"

    send_mail(
        subject="Reset Password - SportMate",
        message=f"Click to reset password: {reset_link}",
        from_email="noreply@sportmate.com",
        recipient_list=[email],
    )

    return Response({"msg": "Reset link sent to email"})


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password(request, uidb64, token):
    new_password = request.data.get('password')

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except:
        return Response({"error": "Invalid link"}, status=400)

    if not PasswordResetTokenGenerator().check_token(user, token):
        return Response({"error": "Token expired"}, status=400)

    user.set_password(new_password)
    user.save()

    return Response({"msg": "Password reset successful"})


# ---------------- CHANGE PASSWORD ----------------
@api_view(['POST'])
@permission_classes([IsAuthenticated])
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
class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class PlayerDetailView(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]


# ---------------- SPORTS ----------------
class SportListView(generics.ListAPIView):
    queryset = Sport.objects.filter(is_active=True)
    serializer_class = SportSerializer
    permission_classes = [AllowAny]
    pagination_class = None  # ← add this line


# ---------------- AI PLAYER MATCHING ----------------
class PlayerListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sport = request.query_params.get('sport')
        lat = float(request.query_params.get('lat'))
        lng = float(request.query_params.get('lng'))
        radius = float(request.query_params.get('radius', 15))

        user = request.user

        players = User.objects.filter(
            is_available=True
        ).exclude(id=user.id)

        if sport:
            players = players.filter(sports__name__iexact=sport)

        results = []

        for p in players.distinct():
            distance = p.distance_to(lat, lng)

            if distance is None or distance > radius:
                continue

            # 🔥 AI MATCHING ALGORITHM
            skill_score = 1 if p.profile.skill_level == user.profile.skill_level else 0.5
            rating_diff = abs(p.profile.rating - user.profile.rating)
            rating_score = max(0, 1 - rating_diff / 5)
            distance_score = max(0, 1 - distance / radius)
            availability_score = 1 if p.is_available else 0

            final_score = (
                0.4 * skill_score +
                0.3 * rating_score +
                0.2 * distance_score +
                0.1 * availability_score
            )

            p.distance_km = round(distance, 2)
            p.match_score = round(final_score, 2)

            results.append(p)

        results.sort(key=lambda x: x.match_score, reverse=True)

        return Response(UserSerializer(results, many=True).data)


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
    permission_classes = [IsAuthenticated]


class CreateMatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        match = Match.objects.create(
            sport_id=request.data['sport'],
            organizer=request.user,
            ground_id=request.data['ground'],
            date_time=request.data['date_time'],
            total_players=request.data['total_players']
        )

        return Response({
            "msg": "Match created",
            "match_id": match.id
        })


class JoinMatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, match_id):
        match = Match.objects.get(id=match_id)

        if match.joined_players.count() >= match.total_players:
            return Response({'error': 'Match full'}, status=400)

        match.joined_players.add(request.user)

        Notification.objects.create(
            user=match.organizer,
            type='match_invite',
            title='Player Joined',
            body=f'{request.user.username} joined your match',
            data={'match_id': match.id}
        )

        return Response({'msg': 'Joined successfully'})


# ---------------- CHAT ----------------
# ---------------- CHAT ----------------
from .utils import create_notification   # 🔥 add this import


class MessageThreadView(generics.ListAPIView):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

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
    permission_classes = [IsAuthenticated]

    def get(self, request):
        count = Message.objects.filter(
            receiver=request.user,
            is_read=False
        ).count()

        return Response({"unread": count})

# ---------------- NOTIFICATIONS ----------------
class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)


# ---------------- AVAILABILITY ----------------
class AvailabilityView(APIView):
    permission_classes = [IsAuthenticated]

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
from django.db.models import Count
from math import radians, sin, cos, sqrt, atan2

from .models import Match, PlayerProfile


class AiMatchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        user_lat = user.latitude
        user_lng = user.longitude

        # fallback if no location
        if not user_lat or not user_lng:
            matches = Match.objects.all()[:10]
            return Response(self.serialize(matches))

        matches = Match.objects.annotate(
            players_count=Count('players')
        ).filter(
            players_count__lt=10  # only not full matches
        )

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

        final_matches = [m[0] for m in results[:10]]

        return Response(self.serialize(final_matches))

    def calculate_distance(self, lat1, lon1, lat2, lon2):
        R = 6371  # km

        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)

        a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))

        return R * c

    def serialize(self, matches):
        data = []

        for m in matches:
            data.append({
                "id": m.id,
                "sport": {
                    "name": m.sport.name
                } if m.sport else None,
                "players_count": m.players.count(),
                "ground": {
                    "name": m.ground.name
                } if m.ground else None,
                "date": m.date
            })

        return data
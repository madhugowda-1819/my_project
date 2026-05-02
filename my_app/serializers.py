from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import (
    Sport, PlayerProfile, AvailabilitySlot,
    Match, Message, Notification, Ground
)

User = get_user_model()


# ------------------------------------------------------------------
# SPORT
# ------------------------------------------------------------------
class SportSerializer(serializers.ModelSerializer):
    nearbyPlayers = serializers.SerializerMethodField()

    class Meta:
        model = Sport
        fields = ['id', 'name', 'icon', 'category', 'is_active', 'nearbyPlayers']

    def get_nearbyPlayers(self, obj):
        return obj.players.count()


# ------------------------------------------------------------------
# 🔥 GROUND (NEW)
# ------------------------------------------------------------------
class GroundSerializer(serializers.ModelSerializer):
    distanceKm = serializers.SerializerMethodField()

    class Meta:
        model = Ground
        fields = [
            'id',
            'name',
            'city',
            'price_per_hour',
            'pitch_type',
            'distanceKm'
        ]

    def get_distanceKm(self, obj):
        return getattr(obj, 'distance_km', None)


# ------------------------------------------------------------------
# AVAILABILITY
# ------------------------------------------------------------------
class AvailabilitySlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = AvailabilitySlot
        fields = ['id', 'day_of_week', 'start_time', 'end_time', 'label']


# ------------------------------------------------------------------
# PLAYER PROFILE
# ------------------------------------------------------------------
class PlayerProfileSerializer(serializers.ModelSerializer):
    winRate = serializers.SerializerMethodField()

    class Meta:
        model = PlayerProfile
        fields = [
            'skill_level',
            'matches_played',
            'matches_won',
            'rating',
            'achievements',
            'winRate'
        ]

    def get_winRate(self, obj):
        return obj.win_rate()


# ------------------------------------------------------------------
# REGISTER
# ------------------------------------------------------------------
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Sport, PlayerProfile

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)

    sport_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Sport.objects.all(),
        source='sports',
        required=False
    )

    class Meta:
        model = User
        fields = ['id', 'full_name', 'email', 'username', 'phone', 'password', 'sport_ids']

    # 🔥 ADD THIS (VERY IMPORTANT)
    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already registered")
        return value

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already taken")
        return value

    def create(self, validated_data):
        sports = validated_data.pop('sports', [])
        password = validated_data.pop('password')

        user = User(**validated_data)
        user.set_password(password)
        user.save()

        if sports:
            user.sports.set(sports)

        PlayerProfile.objects.create(user=user)
        return user


# ------------------------------------------------------------------
# USER
# ------------------------------------------------------------------
class UserSerializer(serializers.ModelSerializer):
    sports = SportSerializer(many=True, read_only=True)
    profile = PlayerProfileSerializer(read_only=True)
    availability_slots = AvailabilitySlotSerializer(many=True, read_only=True)

    distanceKm = serializers.SerializerMethodField()
    isOnline = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id','full_name', 'email', 'username', 'phone', 'avatar', 'bio',
            'city', 'latitude', 'longitude', 'is_available',
            'sports', 'profile', 'availability_slots',
            'distanceKm', 'isOnline'
        ]

    def get_distanceKm(self, obj):
        return getattr(obj, 'distance_km', None)

    def get_isOnline(self, obj):
        return obj.is_user_online()


# ------------------------------------------------------------------
# MATCH (UPDATED 🔥)
# ------------------------------------------------------------------
class MatchSerializer(serializers.ModelSerializer):
    sportName = serializers.CharField(source='sport.name')
    organizer = serializers.CharField(source='organizer.username')

    # 🔥 NEW: Ground info
    groundName = serializers.CharField(source='ground.name')
    groundLocation = serializers.CharField(source='ground.city')

    joinedPlayers = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = [
            'id',
            'sportName',
            'groundName',
            'groundLocation',
            'date_time',
            'total_players',
            'joinedPlayers',
            'organizer',
            'status'
        ]

    def get_joinedPlayers(self, obj):
        return obj.joined_players.count()


# ------------------------------------------------------------------
# MESSAGE
# ------------------------------------------------------------------
class MessageSerializer(serializers.ModelSerializer):
    senderName = serializers.CharField(source='sender.username', read_only=True)

    class Meta:
        model = Message
        fields = [
            'id',
            'sender',
            'senderName',
            'receiver',
            'content',
            'is_read',
            'is_delivered',
            'created_at'
        ]
        read_only_fields = ['sender', 'is_read', 'is_delivered', 'created_at']


# ------------------------------------------------------------------
# NOTIFICATION
# ------------------------------------------------------------------
class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id',
            'type',
            'title',
            'body',
            'priority',
            'is_read',
            'data',
            'created_at'
        ]


# ------------------------------------------------------------------
# AVAILABILITY TOGGLE
# ------------------------------------------------------------------
class AvailabilityToggleSerializer(serializers.Serializer):
    is_available = serializers.BooleanField(required=False)
    slots = AvailabilitySlotSerializer(many=True, required=False)
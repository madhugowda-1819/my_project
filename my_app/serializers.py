from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.validators import RegexValidator
from .models import (
    Sport, PlayerProfile, AvailabilitySlot,
    Match, Message, Notification, Ground
)
from .models import UserSport
from .models import Venue, VenueAmenity, VenueImage, VenueReview, Court
from .models import CourtBooking

User = get_user_model()
phone_validator = RegexValidator(
    regex=r'^\+?[0-9 ()-]{7,20}$',
    message='Enter a valid phone number.',
)


# ------------------------------------------------------------------
# SPORT
# ------------------------------------------------------------------
class SportSerializer(serializers.ModelSerializer):
    nearbyPlayers = serializers.SerializerMethodField()
    active = serializers.BooleanField(source='is_active', read_only=True)

    class Meta:
        model = Sport
        fields = ['id', 'public_id', 'name', 'slug', 'description', 'icon', 'category', 'active', 'is_active', 'created_at', 'nearbyPlayers']

    def get_nearbyPlayers(self, obj):
        return obj.players.count()


# ------------------------------------------------------------------
# 🔥 GROUND (NEW)
# ------------------------------------------------------------------
class GroundSerializer(serializers.ModelSerializer):
    distanceKm = serializers.SerializerMethodField()
    sports = SportSerializer(many=True, read_only=True)

    class Meta:
        model = Ground
        fields = [
            'id', 'public_id',
            'name',
            'city',
            'address',
            'price_per_hour',
            'pitch_type',
            'sports',
            'distanceKm'
        ]

    def get_distanceKm(self, obj):
        distance = getattr(obj, 'distance_km', None)
        return round(distance, 2) if distance is not None else None


class NearbySearchSerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    radius = serializers.FloatField(min_value=0.1, max_value=500, default=15)
    sport = serializers.CharField(required=False)
    skill_level = serializers.ChoiceField(choices=UserSport.SkillLevel.choices, required=False)


class PlayerRecommendationQuerySerializer(NearbySearchSerializer):
    sport = serializers.PrimaryKeyRelatedField(queryset=Sport.objects.filter(is_active=True))
    limit = serializers.IntegerField(min_value=1, max_value=50, default=20)


class CourtSerializer(serializers.ModelSerializer):
    sport = SportSerializer(read_only=True)

    class Meta:
        model = Court
        fields = ['public_id', 'name', 'sport', 'capacity', 'price_per_hour', 'active']


class VenueAmenitySerializer(serializers.ModelSerializer):
    class Meta:
        model = VenueAmenity
        fields = ['parking', 'washroom', 'changing_room', 'drinking_water', 'equipment', 'lighting', 'air_conditioning', 'seating']


class VenueImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = VenueImage
        fields = ['public_id', 'image', 'caption', 'sort_order']


class VenueReviewSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = VenueReview
        fields = ['public_id', 'username', 'rating', 'comment', 'created_at']


class VenueSerializer(serializers.ModelSerializer):
    sports = serializers.SerializerMethodField()
    amenities = VenueAmenitySerializer(read_only=True)
    courts = CourtSerializer(many=True, read_only=True)
    images = VenueImageSerializer(many=True, read_only=True)
    reviews = VenueReviewSerializer(many=True, read_only=True)
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model = Venue
        fields = [
            'public_id', 'name', 'description', 'address', 'city', 'phone', 'email', 'rating',
            'opening_time', 'closing_time', 'active', 'sports', 'amenities', 'courts', 'images',
            'reviews', 'distance_km',
        ]

    def get_sports(self, obj):
        return SportSerializer([item.sport for item in obj.venue_sports.all()], many=True).data

    def get_distance_km(self, obj):
        distance = getattr(obj, 'distance_km', None)
        return round(distance, 2) if distance is not None else None


class VenueDiscoveryQuerySerializer(NearbySearchSerializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)
    radius = serializers.FloatField(min_value=0.1, max_value=500, required=False, default=15)
    city = serializers.CharField(required=False)
    min_rating = serializers.DecimalField(max_digits=3, decimal_places=2, min_value=0, max_value=5, required=False)
    max_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    amenities = serializers.ListField(child=serializers.CharField(), required=False)


class VenueReviewCreateSerializer(serializers.Serializer):
    booking_id = serializers.IntegerField(min_value=1)
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class VenueAvailabilityQuerySerializer(serializers.Serializer):
    date = serializers.DateField()
    sport = serializers.PrimaryKeyRelatedField(queryset=Sport.objects.filter(is_active=True), required=False)
    court = serializers.IntegerField(min_value=1, required=False)
    duration = serializers.IntegerField(required=False, default=60)


class CourtBookingCreateSerializer(serializers.Serializer):
    court_id = serializers.PrimaryKeyRelatedField(source='court', queryset=Court.objects.select_related('venue'))
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()

    def validate(self, attrs):
        if attrs['ends_at'] <= attrs['starts_at']:
            raise serializers.ValidationError({'ends_at': 'ends_at must be after starts_at.'})
        return attrs


class BookingCreateSerializer(serializers.Serializer):
    venue_id = serializers.UUIDField()
    court_id = serializers.UUIDField()
    booking_date = serializers.DateField()
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()


class BookingSerializer(serializers.ModelSerializer):
    venue_id = serializers.UUIDField(source='venue.public_id', read_only=True)
    court_id = serializers.UUIDField(source='court.public_id', read_only=True)
    venue_name = serializers.CharField(source='venue.name', read_only=True)
    court_name = serializers.CharField(source='court.name', read_only=True)

    class Meta:
        model = CourtBooking
        fields = [
            'public_id', 'booking_reference', 'venue_id', 'venue_name', 'court_id', 'court_name',
            'booking_date', 'start_time', 'end_time', 'base_price', 'additional_fees', 'discount',
            'final_amount', 'status', 'cancellation_reason', 'cancelled_at', 'created_at', 'updated_at',
        ]


# ------------------------------------------------------------------
# AVAILABILITY
# ------------------------------------------------------------------
class AvailabilitySlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = AvailabilitySlot
        fields = ['id', 'day_of_week', 'start_time', 'end_time', 'label']

    def validate(self, attrs):
        if attrs['end_time'] <= attrs['start_time']:
            raise serializers.ValidationError('end_time must be after start_time.')
        return attrs


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


class UserSportSerializer(serializers.ModelSerializer):
    sport = SportSerializer(read_only=True)

    class Meta:
        model = UserSport
        fields = ['public_id', 'sport', 'skill_level', 'rating', 'matches_played', 'wins', 'losses', 'preferred']
        read_only_fields = ['public_id', 'matches_played', 'wins', 'losses']


class UserSportWriteSerializer(serializers.Serializer):
    sport_id = serializers.PrimaryKeyRelatedField(source='sport', queryset=Sport.objects.filter(is_active=True))
    skill_level = serializers.ChoiceField(choices=UserSport.SkillLevel.choices)
    rating = serializers.DecimalField(max_digits=3, decimal_places=1, min_value=0, max_value=5, required=False, default=0)
    preferred = serializers.BooleanField(required=False, default=False)


class PlayerProfilePreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlayerProfile
        fields = ['bio', 'availability', 'preferred_distance', 'preferred_play_times', 'profile_visibility']


class PublicPlayerSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='full_name')
    location = serializers.CharField(source='city')
    distance_km = serializers.SerializerMethodField()
    sports = UserSportSerializer(source='user_sports', many=True, read_only=True)
    availability = serializers.BooleanField(source='is_available', read_only=True)

    class Meta:
        model = User
        fields = ['public_id', 'name', 'username', 'location', 'distance_km', 'availability', 'bio', 'sports']

    def get_distance_km(self, obj):
        distance = getattr(obj, 'distance_km', None)
        return round(distance, 1) if distance is not None else None


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


class RegistrationRequestSerializer(serializers.Serializer):
    name = serializers.CharField(source='full_name', max_length=150)
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, validators=[phone_validator])
    password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8)


class LoginRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=254, required=False)
    email = serializers.EmailField(required=False)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        if not attrs.get('identifier') and not attrs.get('email'):
            raise serializers.ValidationError({'identifier': 'Email or username is required.'})
        return attrs


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
            'id', 'public_id', 'full_name', 'email', 'username', 'phone', 'avatar', 'bio',
            'city', 'latitude', 'longitude', 'is_available',
            'sports', 'profile', 'availability_slots',
            'distanceKm', 'isOnline'
        ]

    def get_distanceKm(self, obj):
        return getattr(obj, 'distance_km', None)

    def get_isOnline(self, obj):
        return obj.is_user_online()


class CurrentUserSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='full_name', required=False)
    phone = serializers.CharField(required=False, allow_blank=True, validators=[phone_validator])

    class Meta:
        model = User
        fields = [
            'public_id', 'name', 'username', 'email', 'phone', 'avatar', 'city',
            'latitude', 'longitude', 'bio', 'date_joined', 'is_active', 'account_status',
        ]
        read_only_fields = ['public_id', 'username', 'email', 'date_joined', 'is_active', 'account_status']


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
            'id', 'public_id',
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


class MatchCreateSerializer(serializers.ModelSerializer):
    sport = serializers.PrimaryKeyRelatedField(queryset=Sport.objects.filter(is_active=True))
    ground = serializers.PrimaryKeyRelatedField(queryset=Ground.objects.all())

    class Meta:
        model = Match
        fields = ['sport', 'ground', 'date_time', 'total_players']

    def validate_date_time(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('date_time must be in the future.')
        return value


# ------------------------------------------------------------------
# MESSAGE
# ------------------------------------------------------------------
class MessageSerializer(serializers.ModelSerializer):
    senderName = serializers.CharField(source='sender.username', read_only=True)

    class Meta:
        model = Message
        fields = [
            'id', 'public_id',
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
            'id', 'public_id',
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

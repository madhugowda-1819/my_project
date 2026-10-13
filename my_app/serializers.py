from rest_framework import serializers
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from django.core.validators import RegexValidator
from django.core.files.images import get_image_dimensions
from .models import (
    Sport, PlayerProfile, AvailabilitySlot,
    Match, Message, Notification, Ground, Game, GamePlayer, Conversation, ConversationMember,
    Community, CommunityMember, CommunityPost, CommunityComment, CommunityPostLike,
    Event, EventParticipant, EventReminder, Tournament, TournamentTeam, TournamentTeamMember, TournamentMatch,
    College, CollegeMembership, CollegeChallenge, CollegeResultDispute,
    MatchTimePoll, MatchTimePollOption,
    MatchReplacementInvitation,
    PracticeActivity,
    PracticeCompletion,
    SportsGoal,
    SportChallenge,
    Achievement,
    HealthActivityRecord,
    NotificationPreference, UserDevice,
    PlayerRating,
    MatchReflection,
    Achievement, UserAchievement,
    Report, ModerationAction, UserModeration, VenueOwnerApplication,
)
from .models import UserSport
from .models import Venue, VenueAmenity, VenueImage, VenueReview, Court
from .models import CourtBooking

User = get_user_model()
phone_validator = RegexValidator(
    regex=r'^\+?[0-9 ()-]{7,20}$',
    message='Enter a valid phone number.',
)


class SafeImageField(serializers.ImageField):
    """Reject oversized, malformed, and non-image uploads before persistence."""
    max_upload_size = 5 * 1024 * 1024
    allowed_content_types = {'image/jpeg', 'image/png', 'image/webp'}

    def to_internal_value(self, data):
        if data.size > self.max_upload_size:
            raise serializers.ValidationError('Image must be 5 MB or smaller.')
        content_type = getattr(data, 'content_type', '')
        if content_type and content_type not in self.allowed_content_types:
            raise serializers.ValidationError('Only JPEG, PNG, and WebP images are allowed.')
        value = super().to_internal_value(data)
        try:
            width, height = get_image_dimensions(value)
        except Exception as exc:
            raise serializers.ValidationError('Invalid image content.') from exc
        if not width or not height or width > 8000 or height > 8000:
            raise serializers.ValidationError('Image dimensions are invalid or too large.')
        return value


class ReportCreateSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(choices=Report.TargetType.choices)
    target_public_id = serializers.UUIDField()
    reason = serializers.ChoiceField(choices=Report.Reason.choices)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ReportSerializer(serializers.ModelSerializer):
    reporter_id = serializers.UUIDField(source='reporter.public_id', read_only=True)
    assigned_moderator_id = serializers.UUIDField(source='assigned_moderator.public_id', read_only=True, allow_null=True)

    class Meta:
        model = Report
        fields = ['public_id', 'reporter_id', 'target_type', 'target_public_id', 'reason', 'description', 'status', 'assigned_moderator_id', 'resolution', 'created_at', 'updated_at', 'resolved_at']
        read_only_fields = fields


class ReportUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Report.Status.choices, required=False)
    resolution = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    assigned_moderator_id = serializers.UUIDField(required=False, allow_null=True)
    action = serializers.ChoiceField(choices=ModerationAction.Action.choices, required=False)
    action_reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    ends_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError('At least one update is required.')
        return attrs


class ModerationActionSerializer(serializers.ModelSerializer):
    moderator_id = serializers.UUIDField(source='moderator.public_id', read_only=True)
    report_id = serializers.UUIDField(source='report.public_id', read_only=True, allow_null=True)

    class Meta:
        model = ModerationAction
        fields = ['public_id', 'moderator_id', 'report_id', 'target_type', 'target_public_id', 'action', 'reason', 'previous_state', 'new_state', 'created_at']
        read_only_fields = fields


class AdminUserSerializer(serializers.ModelSerializer):
    public_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = User
        fields = ['public_id', 'username', 'email', 'full_name', 'city', 'account_status', 'is_active', 'is_staff', 'date_joined', 'last_login']


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
            'size',
            'sports',
            'address',
            'maps_place_id',
            'source',
            'distanceKm'
        ]

    def get_distanceKm(self, obj):
        distance = getattr(obj, 'distance_km', None)
        return round(distance, 2) if distance is not None else None


class GroundDiscoverySerializer(serializers.Serializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)
    radius = serializers.FloatField(min_value=0.1, max_value=50, default=10)
    sport = serializers.CharField(required=False, allow_blank=False, max_length=60)


class AutoGameCreateSerializer(serializers.Serializer):
    sport_id = serializers.PrimaryKeyRelatedField(
        source='sport', queryset=Sport.objects.filter(is_active=True), required=False,
    )
    radius = serializers.FloatField(min_value=0.1, max_value=100, required=False)
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)

    def validate(self, attrs):
        if ('latitude' in attrs) != ('longitude' in attrs):
            raise serializers.ValidationError('latitude and longitude must be supplied together.')
        return attrs


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
    # A zero is merely the default for a venue with no reviews. Returning null
    # lets clients display a helpful "New" label instead of a false 0.00 score.
    rating = serializers.SerializerMethodField()
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

    def get_rating(self, obj):
        return float(obj.rating) if obj.rating and obj.rating > 0 else None

    def get_sports(self, obj):
        return SportSerializer([item.sport for item in obj.venue_sports.all()], many=True).data

    def get_distance_km(self, obj):
        distance = getattr(obj, 'distance_km', None)
        return round(distance, 2) if distance is not None else None


class VenueDiscoveryQuerySerializer(NearbySearchSerializer):
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)
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


class RazorpayVerificationSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField(max_length=64)
    razorpay_payment_id = serializers.CharField(max_length=64)
    razorpay_signature = serializers.CharField(max_length=128)


class BookingSerializer(serializers.ModelSerializer):
    venue_id = serializers.UUIDField(source='venue.public_id', read_only=True)
    court_id = serializers.UUIDField(source='court.public_id', read_only=True)
    venue_name = serializers.CharField(source='venue.name', read_only=True)
    court_name = serializers.CharField(source='court.name', read_only=True)
    payment_status = serializers.SerializerMethodField()

    class Meta:
        model = CourtBooking
        fields = [
            'public_id', 'booking_reference', 'venue_id', 'venue_name', 'court_id', 'court_name',
            'booking_date', 'start_time', 'end_time', 'base_price', 'additional_fees', 'discount',
            'final_amount', 'status', 'payment_status', 'cancellation_reason', 'cancelled_at', 'created_at', 'updated_at',
        ]

    def get_payment_status(self, obj):
        payment = getattr(obj, 'payment', None)
        return payment.status if payment else None


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
        fields = ['bio', 'availability', 'preferred_distance', 'preferred_play_times', 'profile_visibility', 'matching_mood']

    def validate_preferred_distance(self, value):
        if value < 1 or value > 500:
            raise serializers.ValidationError('Preferred distance must be between 1 and 500 km.')
        return value

    def validate_preferred_play_times(self, value):
        allowed = {'morning', 'afternoon', 'evening', 'weekend'}
        if not isinstance(value, list) or any(not isinstance(item, str) or item.lower() not in allowed for item in value):
            raise serializers.ValidationError('Use a list containing morning, afternoon, evening, or weekend.')
        return [item.lower() for item in value]

    def validate_availability(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('Availability must be an object.')
        return value


class PublicPlayerSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='full_name')
    location = serializers.CharField(source='city')
    distance_km = serializers.SerializerMethodField()
    sports = UserSportSerializer(source='user_sports', many=True, read_only=True)
    availability = serializers.BooleanField(source='is_available', read_only=True)
    rating_summary = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['public_id', 'name', 'username', 'location', 'distance_km', 'availability', 'bio', 'sports', 'rating_summary']

    def get_distance_km(self, obj):
        distance = getattr(obj, 'distance_km', None)
        return round(distance, 1) if distance is not None else None

    def get_rating_summary(self, obj):
        from django.db.models import Avg, Count
        values = obj.ratings_received.filter(deleted_at__isnull=True).aggregate(
            overall=Avg('rating'), count=Count('id'), playing_performance=Avg('playing_performance'),
            teamwork=Avg('teamwork'), sportsmanship=Avg('sportsmanship'), punctuality=Avg('punctuality'),
        )
        return {
            'average_rating': round(float(values['overall'] or 0), 1), 'rating_count': values['count'],
            'playing_performance': round(float(values['playing_performance'] or 0), 1),
            'teamwork': round(float(values['teamwork'] or 0), 1),
            'sportsmanship': round(float(values['sportsmanship'] or 0), 1),
            'punctuality': round(float(values['punctuality'] or 0), 1),
        }


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
    # Saved at sign-up so discovery results can be catalogued by the player's
    # home city even before location permission is granted.
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8)
    referral_code = serializers.CharField(max_length=20, required=False, allow_blank=True, trim_whitespace=True)
    sport_ids = serializers.PrimaryKeyRelatedField(
        source='sports',
        many=True,
        queryset=Sport.objects.filter(is_active=True),
        required=False,
    )


class VenueOwnerRegistrationSerializer(serializers.Serializer):
    name = serializers.CharField(source='full_name', max_length=150)
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, validators=[phone_validator])
    city = serializers.CharField(max_length=100)
    password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8)
    business_name = serializers.CharField(max_length=200)
    address = serializers.CharField(max_length=255)


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
    # Mobile clients use `name`, while the database field remains `full_name`.
    name = serializers.CharField(source='full_name', required=False)
    sports = SportSerializer(many=True, read_only=True)
    profile = PlayerProfileSerializer(read_only=True)
    availability_slots = AvailabilitySlotSerializer(many=True, read_only=True)

    distanceKm = serializers.SerializerMethodField()
    isOnline = serializers.SerializerMethodField()
    avatar = SafeImageField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            'id', 'public_id', 'name', 'full_name', 'email', 'username', 'phone', 'avatar', 'bio',
            'city', 'latitude', 'longitude', 'is_available',
            'sports', 'profile', 'availability_slots',
            'distanceKm', 'isOnline'
        ]
        read_only_fields = ['id', 'public_id', 'email', 'username', 'sports', 'profile', 'availability_slots', 'distanceKm', 'isOnline']

    def get_distanceKm(self, obj):
        return getattr(obj, 'distance_km', None)

    def get_isOnline(self, obj):
        return obj.is_user_online()


class CurrentUserSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='full_name', required=False)
    phone = serializers.CharField(required=False, allow_blank=True, validators=[phone_validator])
    roles = serializers.SerializerMethodField()
    matching_mood = serializers.SerializerMethodField()
    preferred_distance = serializers.SerializerMethodField()
    preferred_play_times = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'public_id', 'name', 'username', 'email', 'phone', 'avatar', 'city',
            'latitude', 'longitude', 'bio', 'date_joined', 'is_active', 'account_status', 'roles',
            'matching_mood', 'preferred_distance', 'preferred_play_times',
        ]
        read_only_fields = ['public_id', 'username', 'email', 'date_joined', 'is_active', 'account_status']

    def get_roles(self, obj):
        roles = list(obj.groups.values_list('name', flat=True))
        if (obj.is_superuser or obj.has_perm('my_app.view_user')) and 'SportMate Admins' not in roles:
            roles.append('SportMate Admins')
        return roles

    def get_matching_mood(self, obj):
        profile = getattr(obj, 'profile', None)
        return profile.matching_mood if profile else ''

    def get_preferred_distance(self, obj):
        profile = getattr(obj, 'profile', None)
        return profile.preferred_distance if profile else None

    def get_preferred_play_times(self, obj):
        profile = getattr(obj, 'profile', None)
        return profile.preferred_play_times if profile else []


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
    status = serializers.SerializerMethodField()
    is_joined = serializers.SerializerMethodField()
    is_organizer = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = [
            'id', 'public_id',
            'sportName',
            'groundName',
            'groundLocation',
            'date_time',
            'end_date_time',
            'total_players',
            'joinedPlayers',
            'organizer', 'status', 'is_joined', 'is_organizer'
        ]

    def get_joinedPlayers(self, obj):
        return obj.joined_players.count()

    def get_status(self, obj):
        # Legacy matches retain their stored status, but an unprocessed match
        # must never look joinable once its scheduled time has passed.
        if obj.status == 'upcoming':
            if obj.end_date_time <= timezone.now():
                return 'completed'
            if obj.date_time <= timezone.now():
                return 'ongoing'
        return obj.status

    def get_is_joined(self, obj):
        user = self.context.get('request').user if self.context.get('request') else None
        return bool(user and user.is_authenticated and obj.joined_players.filter(pk=user.pk).exists())

    def get_is_organizer(self, obj):
        user = self.context.get('request').user if self.context.get('request') else None
        return bool(user and user.is_authenticated and obj.organizer_id == user.pk)


class MatchParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'public_id', 'username', 'full_name', 'avatar']


class MatchReplacementInvitationSerializer(serializers.ModelSerializer):
    match_id = serializers.IntegerField(source='opportunity.match.id', read_only=True)
    sport = serializers.CharField(source='opportunity.match.sport.name', read_only=True)
    starts_at = serializers.DateTimeField(source='opportunity.match.date_time', read_only=True)
    ends_at = serializers.DateTimeField(source='opportunity.match.end_date_time', read_only=True)
    location = serializers.CharField(source='opportunity.match.ground.city', read_only=True)
    ground = serializers.CharField(source='opportunity.match.ground.name', read_only=True)
    total_players = serializers.IntegerField(source='opportunity.match.total_players', read_only=True)
    joined_players = serializers.SerializerMethodField()
    expires_at = serializers.DateTimeField(source='opportunity.expires_at', read_only=True)

    class Meta:
        model = MatchReplacementInvitation
        fields = ['public_id', 'status', 'match_id', 'sport', 'starts_at', 'ends_at', 'location', 'ground', 'total_players', 'joined_players', 'expires_at']

    def get_joined_players(self, obj):
        return obj.opportunity.match.joined_players.count()


class PracticeCompletionInputSerializer(serializers.Serializer):
    date = serializers.DateField()
    timezone = serializers.CharField(max_length=64, default='UTC')
    completed = serializers.BooleanField()


class PracticeActivityWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    description = serializers.CharField(max_length=240, allow_blank=True, required=False)
    sort_order = serializers.IntegerField(min_value=0, required=False)
    is_active = serializers.BooleanField(required=False)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Activity name cannot be blank.')
        return value


class PracticeActivityCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(max_length=240, allow_blank=True, required=False, default='')
    sort_order = serializers.IntegerField(min_value=0, required=False)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError('Activity name cannot be blank.')
        return value


class PracticeActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = PracticeActivity
        fields = ['public_id', 'name', 'description', 'sort_order', 'is_active', 'is_default']
        read_only_fields = fields


class SportsGoalCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120)
    target = serializers.IntegerField(min_value=1, max_value=100000)
    unit = serializers.CharField(max_length=32)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    progress_source = serializers.ChoiceField(choices=SportsGoal.Source.choices)
    manual_progress = serializers.IntegerField(min_value=0, required=False, default=0)
    reminder_enabled = serializers.BooleanField(required=False, default=False)
    reminder_time = serializers.TimeField(required=False, allow_null=True)
    reminder_timezone = serializers.CharField(max_length=64, required=False, default='Asia/Kolkata')

    def validate(self, attrs):
        if attrs['end_date'] < attrs['start_date']:
            raise serializers.ValidationError({'end_date': 'End date must be on or after start date.'})
        if attrs['progress_source'] != SportsGoal.Source.MANUAL and attrs['manual_progress']:
            raise serializers.ValidationError({'manual_progress': 'Only manual goals accept manually recorded progress.'})
        if attrs['reminder_enabled'] and not attrs.get('reminder_time'):
            raise serializers.ValidationError({'reminder_time': 'Set a time to enable reminders.'})
        return attrs


class SportsGoalUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120, required=False)
    target = serializers.IntegerField(min_value=1, max_value=100000, required=False)
    unit = serializers.CharField(max_length=32, required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    manual_progress = serializers.IntegerField(min_value=0, required=False)
    reminder_enabled = serializers.BooleanField(required=False)
    reminder_time = serializers.TimeField(required=False, allow_null=True)
    reminder_timezone = serializers.CharField(max_length=64, required=False)


class SportsGoalTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[SportsGoal.Status.ACTIVE, SportsGoal.Status.PAUSED, SportsGoal.Status.COMPLETED, SportsGoal.Status.ARCHIVED])


class HealthActivityCreateSerializer(serializers.Serializer):
    metric = serializers.ChoiceField(choices=[
        HealthActivityRecord.Metric.STEPS,
        HealthActivityRecord.Metric.DURATION,
        HealthActivityRecord.Metric.CALORIES,
    ])
    value = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    recorded_date = serializers.DateField()
    timezone = serializers.CharField(max_length=64, default='Asia/Kolkata')


class HealthActivityImportItemSerializer(serializers.Serializer):
    metric = serializers.ChoiceField(choices=[
        HealthActivityRecord.Metric.STEPS,
        HealthActivityRecord.Metric.DURATION,
        HealthActivityRecord.Metric.CALORIES,
    ])
    value = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    recorded_date = serializers.DateField()
    external_record_id = serializers.CharField(max_length=255, allow_blank=False)
    timezone = serializers.CharField(max_length=64, default='UTC')


class TrainingRecommendationInputSerializer(serializers.Serializer):
    sport_id = serializers.IntegerField(min_value=1)
    skill_level = serializers.ChoiceField(choices=UserSport.SkillLevel.choices, required=False)
    goal = serializers.ChoiceField(choices=[
        ('general_fitness', 'General fitness'),
        ('skill_development', 'Skill development'),
        ('endurance', 'Endurance'),
        ('strength', 'Strength'),
        ('flexibility', 'Flexibility'),
    ])
    duration_minutes = serializers.IntegerField(min_value=15, max_value=120)
    days_per_week = serializers.IntegerField(min_value=1, max_value=7, required=False, default=3)

    def validate_sport_id(self, value):
        from .models import Sport
        if not Sport.objects.filter(pk=value, is_active=True).exists():
            raise serializers.ValidationError('Choose an active SportMate sport.')
        return value


class SportChallengeCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True, default='')
    sport_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    kind = serializers.ChoiceField(choices=SportChallenge.Kind.choices)
    metric = serializers.ChoiceField(choices=SportChallenge.Metric.choices)
    target = serializers.IntegerField(min_value=1, max_value=100000)
    eligibility_min_skill = serializers.ChoiceField(choices=UserSport.SkillLevel.choices, required=False, allow_blank=True, default='')
    starts_on = serializers.DateField()
    ends_on = serializers.DateField()
    reward_points = serializers.IntegerField(min_value=0, max_value=1000, required=False, default=0)
    reward_achievement_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs['ends_on'] < attrs['starts_on']:
            raise serializers.ValidationError({'ends_on': 'End date must be on or after the start date.'})
        if attrs.get('sport_id') and not Sport.objects.filter(pk=attrs['sport_id'], is_active=True).exists():
            raise serializers.ValidationError({'sport_id': 'Choose an active sport.'})
        achievement_id = attrs.get('reward_achievement_id')
        if achievement_id and not Achievement.objects.filter(public_id=achievement_id, is_active=True).exists():
            raise serializers.ValidationError({'reward_achievement_id': 'Choose an active existing achievement.'})
        return attrs


class ChallengeJoinSerializer(serializers.Serializer):
    team_public_id = serializers.UUIDField(required=False)


class ChallengeAdminUpdateSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class HealthActivityImportSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=[HealthActivityRecord.Source.APPLE_HEALTH, HealthActivityRecord.Source.HEALTH_CONNECT])
    records = HealthActivityImportItemSerializer(many=True, min_length=1, max_length=500)


class HealthSourceDisconnectSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=[HealthActivityRecord.Source.APPLE_HEALTH, HealthActivityRecord.Source.HEALTH_CONNECT])


class MatchCreateSerializer(serializers.ModelSerializer):
    sport = serializers.PrimaryKeyRelatedField(queryset=Sport.objects.filter(is_active=True))
    ground = serializers.PrimaryKeyRelatedField(queryset=Ground.objects.all())
    end_date_time = serializers.DateTimeField(required=False)

    class Meta:
        model = Match
        fields = ['sport', 'ground', 'date_time', 'end_date_time', 'total_players']

    def validate_date_time(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError('date_time must be in the future.')
        return value

    def validate(self, attrs):
        # Older clients send only the start time; retain compatibility with a
        # one-hour same-day match while new clients provide the explicit end.
        end = attrs.get('end_date_time') or attrs['date_time'] + timedelta(hours=1)
        if end <= attrs['date_time']:
            raise serializers.ValidationError({'end_date_time': 'End time must be after the match start time.'})
        attrs['end_date_time'] = end
        return attrs


class MatchTimePollOptionInputSerializer(serializers.Serializer):
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField(required=False)


class MatchTimePollCreateSerializer(serializers.Serializer):
    deadline = serializers.DateTimeField()
    options = MatchTimePollOptionInputSerializer(many=True, min_length=2, max_length=8)

    def validate(self, attrs):
        def has_timezone(value):
            return isinstance(value, str) and (value.endswith('Z') or '+' in value[10:] or '-' in value[10:])
        raw_deadline = self.initial_data.get('deadline')
        if not has_timezone(raw_deadline):
            raise serializers.ValidationError({'deadline': 'Include a timezone offset, for example +05:30 or Z.'})
        raw_options = self.initial_data.get('options', [])
        for index, option in enumerate(raw_options):
            if not has_timezone(option.get('starts_at')) or ('ends_at' in option and not has_timezone(option.get('ends_at'))):
                raise serializers.ValidationError({'options': f'Option {index + 1} must include a timezone offset.'})
        return attrs


class MatchTimePollVoteSerializer(serializers.Serializer):
    option_id = serializers.UUIDField()


class MatchTimePollFinalizeSerializer(serializers.Serializer):
    option_id = serializers.UUIDField(required=False)
    expected_version = serializers.IntegerField(required=False, min_value=1)


# ------------------------------------------------------------------
# COURT-BACKED GAMES
# ------------------------------------------------------------------
class GameCreateSerializer(serializers.Serializer):
    sport_id = serializers.PrimaryKeyRelatedField(source='sport', queryset=Sport.objects.filter(is_active=True))
    venue_id = serializers.UUIDField()
    court_id = serializers.UUIDField()
    game_date = serializers.DateField()
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    min_players = serializers.IntegerField(min_value=1, default=2)
    max_players = serializers.IntegerField(min_value=1)
    skill_level = serializers.ChoiceField(choices=UserSport.SkillLevel.choices, required=False, allow_blank=True, default='')
    description = serializers.CharField(required=False, allow_blank=True, max_length=3000, default='')
    visibility = serializers.ChoiceField(choices=Game.Visibility.choices, required=False, default=Game.Visibility.PUBLIC)

    def validate(self, attrs):
        if attrs['game_date'] < timezone.localdate():
            raise serializers.ValidationError({'game_date': 'Games cannot be scheduled in the past.'})
        if attrs['end_time'] <= attrs['start_time']:
            raise serializers.ValidationError({'end_time': 'end_time must be after start_time.'})
        if attrs['min_players'] > attrs['max_players']:
            raise serializers.ValidationError({'min_players': 'min_players cannot exceed max_players.'})
        return attrs


class GameUpdateSerializer(serializers.Serializer):
    min_players = serializers.IntegerField(min_value=1, required=False)
    max_players = serializers.IntegerField(min_value=1, required=False)
    skill_level = serializers.ChoiceField(choices=UserSport.SkillLevel.choices, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True, max_length=3000)
    visibility = serializers.ChoiceField(choices=Game.Visibility.choices, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError('Provide at least one editable game field.')
        return attrs


class GameQuerySerializer(serializers.Serializer):
    sport = serializers.IntegerField(min_value=1, required=False)
    venue = serializers.UUIDField(required=False)
    city = serializers.CharField(max_length=100, required=False)
    date = serializers.DateField(required=False)
    skill_level = serializers.ChoiceField(choices=UserSport.SkillLevel.choices, required=False)
    status = serializers.ChoiceField(choices=Game.Status.choices, required=False)
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)
    radius = serializers.FloatField(min_value=0.1, max_value=500, required=False, default=15)
    upcoming = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if ('latitude' in attrs) != ('longitude' in attrs):
            raise serializers.ValidationError('latitude and longitude must be provided together.')
        return attrs


class GameRecommendationQuerySerializer(serializers.Serializer):
    sport = serializers.PrimaryKeyRelatedField(queryset=Sport.objects.filter(is_active=True), required=False)
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)
    radius = serializers.FloatField(min_value=0.1, max_value=500, required=False, default=20)
    date = serializers.DateField(required=False)
    skill_level = serializers.ChoiceField(choices=UserSport.SkillLevel.choices, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=50, required=False, default=20)

    def validate(self, attrs):
        if ('latitude' in attrs) != ('longitude' in attrs):
            raise serializers.ValidationError('latitude and longitude must be provided together.')
        if attrs.get('date') and attrs['date'] < timezone.localdate():
            raise serializers.ValidationError({'date': 'date cannot be in the past.'})
        return attrs


class MatchMyMoodQuerySerializer(GameRecommendationQuerySerializer):
    availability = serializers.ChoiceField(
        choices=['any', 'morning', 'afternoon', 'evening', 'weekend'],
        required=False,
        default='any',
    )
    mood = serializers.ChoiceField(choices=PlayerProfile.MOOD_CHOICES, required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if ('latitude' in attrs) != ('longitude' in attrs):
            raise serializers.ValidationError('latitude and longitude must be provided together.')
        return attrs


class GamePlayerSerializer(serializers.ModelSerializer):
    player = serializers.SerializerMethodField()

    class Meta:
        model = GamePlayer
        fields = ['public_id', 'player', 'status', 'joined_at', 'left_at']

    def get_player(self, obj):
        return PublicPlayerSerializer(obj.user, context=self.context).data


class GameSerializer(serializers.ModelSerializer):
    sport = serializers.SerializerMethodField()
    venue = serializers.SerializerMethodField()
    court = serializers.SerializerMethodField()
    host = serializers.SerializerMethodField()
    player_count = serializers.SerializerMethodField()
    available_slots = serializers.SerializerMethodField()
    players = serializers.SerializerMethodField()
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model = Game
        fields = [
            'public_id', 'game_reference', 'sport', 'venue', 'court', 'host',
            'game_date', 'start_time', 'end_time', 'min_players', 'max_players',
            'skill_level', 'description', 'visibility', 'status', 'player_count',
            'available_slots', 'players', 'created_at', 'updated_at',
            'distance_km',
        ]

    def get_sport(self, obj):
        return {'id': obj.sport_id, 'public_id': str(obj.sport.public_id), 'name': obj.sport.name, 'slug': obj.sport.slug}

    def get_venue(self, obj):
        return {'public_id': str(obj.venue.public_id), 'name': obj.venue.name, 'city': obj.venue.city, 'address': obj.venue.address}

    def get_court(self, obj):
        return {'public_id': str(obj.court.public_id), 'name': obj.court.name, 'sport': obj.court.sport.slug}

    def get_host(self, obj):
        return PublicPlayerSerializer(obj.host, context=self.context).data

    def get_player_count(self, obj):
        return getattr(obj, 'confirmed_player_count', None) or obj.game_players.filter(status=GamePlayer.Status.CONFIRMED).count()

    def get_available_slots(self, obj):
        return max(0, obj.max_players - self.get_player_count(obj))

    def get_players(self, obj):
        players = [item for item in obj.game_players.all() if item.status == GamePlayer.Status.CONFIRMED]
        return GamePlayerSerializer(players, many=True, context=self.context).data

    def get_distance_km(self, obj):
        distance = getattr(obj, 'distance_km', None)
        return round(distance, 2) if distance is not None else None


# ------------------------------------------------------------------
# MESSAGE
# ------------------------------------------------------------------
class ConversationCreateSerializer(serializers.Serializer):
    type = serializers.CharField(max_length=20)
    # New clients use an opaque public identifier.  Keep user_id temporarily
    # for older mobile builds that still send the internal primary key.
    user_public_id = serializers.UUIDField(required=False)
    user_id = serializers.IntegerField(min_value=1, required=False)
    game_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        conversation_type = attrs['type'].strip().lower()
        aliases = {'one_to_one': Conversation.Type.ONE_TO_ONE, 'game_group': Conversation.Type.GAME_GROUP}
        if conversation_type not in aliases:
            raise serializers.ValidationError({'type': 'type must be ONE_TO_ONE or GAME_GROUP.'})
        attrs['type'] = aliases[conversation_type]
        if attrs['type'] == Conversation.Type.ONE_TO_ONE:
            if not attrs.get('user_public_id') and not attrs.get('user_id'):
                raise serializers.ValidationError({
                    'user_public_id': 'A player public ID is required for a direct conversation.',
                })
        elif not attrs.get('game_id'):
            raise serializers.ValidationError({'game_id': 'game_id is required for this conversation type.'})
        return attrs


class ConversationMessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=5000)


class ConversationMessageSerializer(serializers.ModelSerializer):
    sender = serializers.UUIDField(source='sender.public_id', read_only=True)
    sender_name = serializers.CharField(source='sender.username', read_only=True)
    content = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ['public_id', 'sender', 'sender_name', 'message_type', 'content', 'created_at', 'updated_at', 'deleted_at', 'is_edited']

    def get_content(self, obj):
        return 'This message was deleted.' if obj.deleted_at else obj.content


# ------------------------------------------------------------------
# COMMUNITIES
# ------------------------------------------------------------------
class CommunityCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(required=False, allow_blank=True, max_length=5000, default='')
    sport_id = serializers.PrimaryKeyRelatedField(source='sport', queryset=Sport.objects.filter(is_active=True), required=False, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False, allow_null=True)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False, allow_null=True)
    visibility = serializers.ChoiceField(choices=Community.Visibility.choices, required=False, default=Community.Visibility.PUBLIC)
    is_club = serializers.BooleanField(required=False, default=False)
    membership_open = serializers.BooleanField(required=False, default=True)
    avatar = SafeImageField(required=False, allow_null=True)
    cover_image = SafeImageField(required=False, allow_null=True)

    def validate(self, attrs):
        if ('latitude' in attrs) != ('longitude' in attrs):
            raise serializers.ValidationError('latitude and longitude must be provided together.')
        return attrs


class CommunityUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False)
    description = serializers.CharField(max_length=5000, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    visibility = serializers.ChoiceField(choices=Community.Visibility.choices, required=False)
    is_active = serializers.BooleanField(required=False)
    membership_open = serializers.BooleanField(required=False)
    avatar = SafeImageField(required=False, allow_null=True)
    cover_image = SafeImageField(required=False, allow_null=True)


class ClubMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[CommunityMember.Role.CAPTAIN, CommunityMember.Role.MEMBER])


class ClubInviteSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(min_value=1)


class CommunityPostCreateSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=5000)
    post_type = serializers.ChoiceField(choices=CommunityPost.Type.choices, required=False, default=CommunityPost.Type.TEXT)
    game_id = serializers.UUIDField(required=False)


class CommunityContentSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=5000)


class CommunityMemberSerializer(serializers.ModelSerializer):
    user = serializers.UUIDField(source='user.public_id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    name = serializers.CharField(source='user.full_name', read_only=True)
    user_id = serializers.IntegerField(source='user.id', read_only=True)

    class Meta:
        model = CommunityMember
        fields = ['user', 'user_id', 'username', 'name', 'role', 'status', 'joined_at', 'updated_at']


class CommunitySerializer(serializers.ModelSerializer):
    sport = serializers.CharField(source='sport.name', read_only=True)
    owner = serializers.UUIDField(source='owner.public_id', read_only=True)
    distance_km = serializers.SerializerMethodField()
    member_role = serializers.SerializerMethodField()
    sport_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = Community
        fields = ['public_id', 'name', 'slug', 'description', 'owner', 'sport', 'sport_id', 'city', 'visibility', 'avatar', 'cover_image', 'is_club', 'membership_open', 'member_count', 'is_active', 'created_at', 'updated_at', 'distance_km', 'member_role']

    def get_distance_km(self, obj):
        value = getattr(obj, 'distance_km', None)
        return round(value, 2) if value is not None else None

    def get_member_role(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
        member = CommunityMember.objects.filter(community=obj, user=request.user, status=CommunityMember.Status.ACTIVE).first()
        return member.role if member else None


class CommunityPostSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source='author.username', read_only=True)
    like_count = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()

    class Meta:
        model = CommunityPost
        fields = ['public_id', 'author', 'content', 'post_type', 'game', 'created_at', 'updated_at', 'deleted_at', 'like_count', 'comment_count']

    def get_content(self, obj):
        return 'This post was deleted.' if obj.deleted_at else obj.content

    def get_like_count(self, obj):
        return getattr(obj, 'like_count', None) if getattr(obj, 'like_count', None) is not None else obj.likes.count()

    def get_comment_count(self, obj):
        return getattr(obj, 'comment_count', None) if getattr(obj, 'comment_count', None) is not None else obj.comments.filter(deleted_at__isnull=True).count()


class CommunityCommentSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source='author.username', read_only=True)
    content = serializers.SerializerMethodField()

    class Meta:
        model = CommunityComment
        fields = ['public_id', 'author', 'content', 'created_at', 'updated_at', 'deleted_at']

    def get_content(self, obj):
        return 'This comment was deleted.' if obj.deleted_at else obj.content

class EventCreateSerializer(serializers.Serializer):
    title=serializers.CharField(max_length=200); description=serializers.CharField(required=False,allow_blank=True,default=''); sport_id=serializers.PrimaryKeyRelatedField(source='sport',queryset=Sport.objects.filter(is_active=True)); community_id=serializers.UUIDField(required=False); venue_id=serializers.UUIDField(); court_id=serializers.UUIDField(required=False,allow_null=True); game_id=serializers.UUIDField(required=False,allow_null=True); event_date=serializers.DateField(); start_time=serializers.TimeField(); end_time=serializers.TimeField(); maximum_participants=serializers.IntegerField(min_value=1); minimum_participants=serializers.IntegerField(min_value=1,default=1); skill_level=serializers.ChoiceField(choices=UserSport.SkillLevel.choices,required=False,allow_blank=True,default=''); visibility=serializers.ChoiceField(choices=Event.Visibility.choices,default=Event.Visibility.PUBLIC); event_type=serializers.ChoiceField(choices=Event.Type.choices,default=Event.Type.MEETUP); registration_deadline=serializers.DateTimeField()
    def validate_registration_deadline(self,value):
        return value
    def validate(self,attrs):
        raw=self.initial_data.get('registration_deadline')
        if not isinstance(raw,str) or not (raw.endswith('Z') or '+' in raw[10:] or '-' in raw[10:]):
            raise serializers.ValidationError({'registration_deadline':'Include a timezone offset, such as +05:30 or Z.'})
        return attrs
class EventSerializer(serializers.ModelSerializer):
    registered_count=serializers.SerializerMethodField(); available_slots=serializers.SerializerMethodField()
    sport_name=serializers.CharField(source='sport.name',read_only=True); venue_name=serializers.CharField(source='venue.name',read_only=True); venue_city=serializers.CharField(source='venue.city',read_only=True)
    starts_at=serializers.DateTimeField(read_only=True); ends_at=serializers.DateTimeField(read_only=True)
    registration_status=serializers.SerializerMethodField(); reminder_at=serializers.SerializerMethodField()
    is_organizer=serializers.SerializerMethodField()
    class Meta:
        model=Event
        fields=['public_id','event_reference','title','description','sport','sport_name','community','venue','venue_name','venue_city','court','event_date','start_time','end_time','starts_at','ends_at','maximum_participants','minimum_participants','skill_level','visibility','event_type','status','registration_deadline','registered_count','available_slots','registration_status','reminder_at','is_organizer']
        read_only_fields=fields
    def get_registered_count(self,obj): return obj.participants.filter(status='registered').count()
    def get_available_slots(self,obj): return max(0,obj.maximum_participants-self.get_registered_count(obj))
    def get_registration_status(self,obj):
        request=self.context.get('request')
        if not request or not request.user.is_authenticated:return None
        participant=obj.participants.filter(user=request.user).first()
        return participant.status if participant else 'not_registered'
    def get_reminder_at(self,obj):
        request=self.context.get('request')
        if not request or not request.user.is_authenticated:return None
        reminder=obj.reminders.filter(user=request.user,cancelled_at__isnull=True,sent_at__isnull=True).first()
        return reminder.remind_at if reminder else None
    def get_is_organizer(self,obj):
        request=self.context.get('request')
        return bool(request and request.user.is_authenticated and obj.organizer_id==request.user.id)
class EventParticipantSerializer(serializers.ModelSerializer):
    username=serializers.CharField(source='user.username',read_only=True)
    class Meta: model=EventParticipant; fields=['username','status','joined_at','left_at']


class EventReminderSerializer(serializers.ModelSerializer):
    class Meta:
        model=EventReminder
        fields=['public_id','remind_at','sent_at','cancelled_at']
        read_only_fields=fields
class TournamentCreateSerializer(serializers.Serializer):
    name=serializers.CharField(max_length=200); sport_id=serializers.PrimaryKeyRelatedField(source='sport',queryset=Sport.objects.filter(is_active=True)); venue_id=serializers.UUIDField(); start_date=serializers.DateField(); end_date=serializers.DateField(); registration_deadline=serializers.DateTimeField(); maximum_teams=serializers.IntegerField(min_value=2); format=serializers.ChoiceField(choices=Tournament.Format.choices,default=Tournament.Format.SINGLE_ELIMINATION); skill_level=serializers.ChoiceField(choices=UserSport.SkillLevel.choices,required=False,allow_blank=True,default='')
    def validate(self, attrs):
        raw=self.initial_data.get('registration_deadline')
        if not isinstance(raw,str) or not (raw.endswith('Z') or '+' in raw[10:] or '-' in raw[10:]):
            raise serializers.ValidationError({'registration_deadline':'Include a timezone offset, such as +05:30 or Z.'})
        if attrs['start_date'] < timezone.localdate():
            raise serializers.ValidationError({'start_date': 'Tournament start date must not be in the past.'})
        if attrs['end_date'] < attrs['start_date']:
            raise serializers.ValidationError({'end_date': 'End date must be on or after the start date.'})
        if attrs['registration_deadline'] <= timezone.now() or attrs['registration_deadline'].date() > attrs['start_date']:
            raise serializers.ValidationError({'registration_deadline': 'Deadline must be in the future and on or before the tournament start date.'})
        return attrs


class TournamentRegistrationSerializer(serializers.Serializer):
    team_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default='')
class TournamentSerializer(serializers.ModelSerializer):
    sport_name=serializers.CharField(source='sport.name',read_only=True); venue_name=serializers.CharField(source='venue.name',read_only=True); venue_city=serializers.CharField(source='venue.city',read_only=True)
    registration_status=serializers.SerializerMethodField()
    class Meta:
        model=Tournament
        fields=['public_id','tournament_reference','name','sport','sport_name','venue','venue_name','venue_city','start_date','end_date','registration_deadline','maximum_teams','format','skill_level','status','registration_status','is_inter_college']
        read_only_fields=fields
    def get_registration_status(self,obj):
        request=self.context.get('request')
        if not request or not request.user.is_authenticated:return None
        if obj.teams.filter(members__user=request.user,members__status=TournamentTeamMember.Status.ACTIVE).exists():return 'registered'
        return 'not_registered'
class TournamentTeamSerializer(serializers.ModelSerializer):
    class Meta: model=TournamentTeam; fields=['public_id','name','captain','seed','status']
class TournamentMatchSerializer(serializers.ModelSerializer):
    class Meta: model=TournamentMatch; fields=['public_id','round_number','match_number','team_a','team_b','score_a','score_b','winner','status']


class CollegeSubmitSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=180)
    city = serializers.CharField(max_length=100)
    state = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    country = serializers.CharField(max_length=100, required=False, default='India')
    description = serializers.CharField(required=False, allow_blank=True, default='')
    approved_email_domain = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    verification_evidence = serializers.URLField(max_length=500, required=False, allow_blank=True, default='')

    def validate_approved_email_domain(self, value):
        value = value.strip().lower().lstrip('@')
        if value and ('.' not in value or ' ' in value or '/' in value):
            raise serializers.ValidationError('Enter a valid email domain without @.')
        return value


class CollegeSerializer(serializers.ModelSerializer):
    active_members = serializers.SerializerMethodField()
    class Meta:
        model = College
        fields = ['public_id', 'name', 'slug', 'city', 'state', 'country', 'description', 'approved_email_domain', 'verification_status', 'rejection_reason', 'active_members']
        read_only_fields = fields
    def get_active_members(self, obj):
        return obj.memberships.filter(status=CollegeMembership.Status.ACTIVE).count()


class CollegeMembershipSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    class Meta:
        model = CollegeMembership
        fields = ['public_id', 'user', 'username', 'role', 'status', 'created_at']
        read_only_fields = fields


class CollegeChallengeSerializer(serializers.ModelSerializer):
    challenger_name = serializers.CharField(source='challenger.name', read_only=True)
    challenged_name = serializers.CharField(source='challenged.name', read_only=True)
    sport_name = serializers.CharField(source='sport.name', read_only=True)
    tournament_id = serializers.UUIDField(source='tournament.public_id', read_only=True)
    class Meta:
        model = CollegeChallenge
        fields = ['public_id', 'challenger', 'challenged', 'challenger_name', 'challenged_name', 'sport', 'sport_name', 'message', 'status', 'tournament_id', 'created_at']
        read_only_fields = fields


class CollegeChallengeCreateSerializer(serializers.Serializer):
    challenged_college_id = serializers.UUIDField()
    sport_id = serializers.PrimaryKeyRelatedField(source='sport', queryset=Sport.objects.filter(is_active=True))
    message = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')


class ChallengeAnswerSerializer(serializers.Serializer):
    accept = serializers.BooleanField()
    venue_id = serializers.UUIDField(required=False)
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    registration_deadline = serializers.DateTimeField(required=False)


class CollegeTeamCreateSerializer(serializers.Serializer):
    college_id = serializers.UUIDField()
    name = serializers.CharField(max_length=120)


class CollegeRosterAddSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()


class CollegeResultDisputeSerializer(serializers.ModelSerializer):
    raised_by_name = serializers.CharField(source='raised_by.username', read_only=True)
    class Meta:
        model = CollegeResultDispute
        fields = ['public_id', 'match', 'raised_by', 'raised_by_name', 'reason', 'status', 'resolution', 'created_at']
        read_only_fields = ['public_id', 'match', 'raised_by', 'raised_by_name', 'status', 'resolution', 'created_at']


class CollegeDisputeCreateSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class CollegeDisputeResolveSerializer(serializers.Serializer):
    resolution = serializers.CharField(max_length=2000)
    dismiss = serializers.BooleanField(required=False, default=False)


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

class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model=NotificationPreference
        fields=['game_notifications','booking_notifications','chat_notifications','community_notifications','event_notifications','tournament_notifications','marketing_notifications']

class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model=UserDevice
        fields=['public_id','platform','device_name','is_active','last_used_at','created_at']

class UserDeviceCreateSerializer(serializers.Serializer):
    device_token=serializers.CharField(max_length=512,trim_whitespace=True)
    platform=serializers.ChoiceField(choices=UserDevice.Platform.choices)
    device_name=serializers.CharField(required=False,allow_blank=True,max_length=120)
class PlayerRatingSerializer(serializers.ModelSerializer):
    reviewer=serializers.CharField(source='reviewer.username',read_only=True)
    class Meta: model=PlayerRating;fields=['public_id','reviewer','rating','playing_performance','teamwork','sportsmanship','punctuality','review','game','created_at','updated_at']
class PlayerRatingCreateSerializer(serializers.Serializer):
    game_id=serializers.UUIDField()
    # Accept the old one-score payload during client rollout.  It is expanded
    # to four categories, while all new clients must send each category.
    rating=serializers.IntegerField(min_value=1,max_value=5,required=False,write_only=True)
    playing_performance=serializers.IntegerField(min_value=1,max_value=5,required=False)
    teamwork=serializers.IntegerField(min_value=1,max_value=5,required=False)
    sportsmanship=serializers.IntegerField(min_value=1,max_value=5,required=False)
    punctuality=serializers.IntegerField(min_value=1,max_value=5,required=False)
    review=serializers.CharField(required=False,allow_blank=True,max_length=2000,trim_whitespace=True)

    def validate(self, attrs):
        categories = ('playing_performance', 'teamwork', 'sportsmanship', 'punctuality')
        missing = [field for field in categories if field not in attrs]
        if missing:
            if len(missing) == len(categories) and 'rating' in attrs:
                for field in categories:
                    attrs[field] = attrs['rating']
            else:
                raise serializers.ValidationError({field: ['This category is required.'] for field in missing})
        attrs.pop('rating', None)
        return attrs


class MatchReflectionSerializer(serializers.ModelSerializer):
    game_id = serializers.UUIDField(source='game.public_id', read_only=True)
    game_reference = serializers.CharField(source='game.game_reference', read_only=True)
    sport = serializers.CharField(source='game.sport.name', read_only=True)
    game_date = serializers.DateField(source='game.game_date', read_only=True)
    venue = serializers.CharField(source='game.venue.name', read_only=True)
    editable_until = serializers.SerializerMethodField()

    class Meta:
        model = MatchReflection
        fields = ['public_id', 'game_id', 'game_reference', 'sport', 'game_date', 'venue',
                  'enjoyment', 'teamwork', 'improvement', 'note', 'created_at', 'updated_at', 'editable_until']
        read_only_fields = fields

    def get_editable_until(self, obj):
        return (obj.created_at + timedelta(hours=24)).isoformat()


class MatchReflectionInputSerializer(serializers.Serializer):
    enjoyment = serializers.IntegerField(min_value=1, max_value=5)
    teamwork = serializers.IntegerField(min_value=1, max_value=5)
    improvement = serializers.IntegerField(min_value=1, max_value=5)
    note = serializers.CharField(required=False, allow_blank=True, max_length=280, trim_whitespace=True)


class AchievementSerializer(serializers.ModelSerializer):
    class Meta: model=Achievement;fields=['public_id','code','name','description','category','icon','target_value','is_active']
class UserAchievementSerializer(serializers.ModelSerializer):
    achievement=AchievementSerializer(read_only=True)
    class Meta: model=UserAchievement;fields=['achievement','progress','unlocked','unlocked_at','updated_at']


# ------------------------------------------------------------------
# AVAILABILITY TOGGLE
# ------------------------------------------------------------------
class AvailabilityToggleSerializer(serializers.Serializer):
    is_available = serializers.BooleanField(required=False)
    slots = AvailabilitySlotSerializer(many=True, required=False)

from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.validators import RegexValidator
from django.core.files.images import get_image_dimensions
from .models import (
    Sport, PlayerProfile, AvailabilitySlot,
    Match, Message, Notification, Ground, Game, GamePlayer, Conversation, ConversationMember,
    Community, CommunityMember, CommunityPost, CommunityComment, CommunityPostLike,
    Event, EventParticipant, Tournament, TournamentTeam, TournamentMatch,
    NotificationPreference, UserDevice,
    PlayerRating,
    Achievement, UserAchievement,
    Report, ModerationAction, UserModeration,
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
    sport_ids = serializers.PrimaryKeyRelatedField(
        source='sports',
        many=True,
        queryset=Sport.objects.filter(is_active=True),
        required=False,
    )


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


class CommunityPostCreateSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=5000)
    post_type = serializers.ChoiceField(choices=CommunityPost.Type.choices, required=False, default=CommunityPost.Type.TEXT)
    game_id = serializers.UUIDField(required=False)


class CommunityContentSerializer(serializers.Serializer):
    content = serializers.CharField(trim_whitespace=False, max_length=5000)


class CommunityMemberSerializer(serializers.ModelSerializer):
    user = serializers.UUIDField(source='user.public_id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = CommunityMember
        fields = ['user', 'username', 'role', 'status', 'joined_at', 'updated_at']


class CommunitySerializer(serializers.ModelSerializer):
    sport = serializers.CharField(source='sport.name', read_only=True)
    owner = serializers.UUIDField(source='owner.public_id', read_only=True)
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model = Community
        fields = ['public_id', 'name', 'slug', 'description', 'owner', 'sport', 'city', 'visibility', 'avatar', 'cover_image', 'member_count', 'is_active', 'created_at', 'updated_at', 'distance_km']

    def get_distance_km(self, obj):
        value = getattr(obj, 'distance_km', None)
        return round(value, 2) if value is not None else None


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
class EventSerializer(serializers.ModelSerializer):
    registered_count=serializers.SerializerMethodField(); available_slots=serializers.SerializerMethodField()
    class Meta: model=Event; fields=['public_id','event_reference','title','description','sport','community','venue','court','event_date','start_time','end_time','maximum_participants','minimum_participants','skill_level','visibility','event_type','status','registration_deadline','registered_count','available_slots']
    def get_registered_count(self,obj): return obj.participants.filter(status='registered').count()
    def get_available_slots(self,obj): return max(0,obj.maximum_participants-self.get_registered_count(obj))
class EventParticipantSerializer(serializers.ModelSerializer):
    username=serializers.CharField(source='user.username',read_only=True)
    class Meta: model=EventParticipant; fields=['username','status','joined_at','left_at']
class TournamentCreateSerializer(serializers.Serializer):
    name=serializers.CharField(max_length=200); sport_id=serializers.PrimaryKeyRelatedField(source='sport',queryset=Sport.objects.filter(is_active=True)); venue_id=serializers.UUIDField(); start_date=serializers.DateField(); end_date=serializers.DateField(); registration_deadline=serializers.DateTimeField(); maximum_teams=serializers.IntegerField(min_value=2); format=serializers.ChoiceField(choices=Tournament.Format.choices,default=Tournament.Format.SINGLE_ELIMINATION); skill_level=serializers.ChoiceField(choices=UserSport.SkillLevel.choices,required=False,allow_blank=True,default='')
class TournamentSerializer(serializers.ModelSerializer):
    class Meta: model=Tournament; fields=['public_id','tournament_reference','name','sport','venue','start_date','end_date','registration_deadline','maximum_teams','format','skill_level','status']
class TournamentTeamSerializer(serializers.ModelSerializer):
    class Meta: model=TournamentTeam; fields=['public_id','name','captain','seed','status']
class TournamentMatchSerializer(serializers.ModelSerializer):
    class Meta: model=TournamentMatch; fields=['public_id','round_number','match_number','team_a','team_b','score_a','score_b','winner','status']


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
    class Meta: model=PlayerRating;fields=['public_id','reviewer','rating','review','game','created_at','updated_at']
class PlayerRatingCreateSerializer(serializers.Serializer):
    game_id=serializers.UUIDField();rating=serializers.IntegerField(min_value=1,max_value=5);review=serializers.CharField(required=False,allow_blank=True,max_length=2000)
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

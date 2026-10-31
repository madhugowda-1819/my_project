import math
import uuid
from datetime import timedelta
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify


class TimestampedPublicModel(models.Model):
    """Common fields for resources returned by the public API."""
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# USER
# ---------------------------------------------------------------------------
class User(AbstractUser):
    class AccountStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        SUSPENDED = 'suspended', 'Suspended'
        DEACTIVATED = 'deactivated', 'Deactivated'

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    player_id = models.CharField(max_length=32, unique=True, null=True, blank=True, editable=False, db_index=True)
    venue_owner_id = models.CharField(max_length=32, unique=True, null=True, blank=True, editable=False, db_index=True)
    full_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(unique=True)
    referral_code = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)
    phone = models.CharField(max_length=20, blank=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)

    # Location
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    city = models.CharField(max_length=100, blank=True)

    # Sports
    sports = models.ManyToManyField('Sport', related_name='players', blank=True)

    # Status
    is_available = models.BooleanField(default=True)
    is_online = models.BooleanField(default=False)
    account_status = models.CharField(
        max_length=16,
        choices=AccountStatus.choices,
        default=AccountStatus.ACTIVE,
        db_index=True,
    )

    # 🔥 NEW (for live status)
    last_seen = models.DateTimeField(auto_now=True)

    bio = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def __str__(self):
        return self.email

    def distance_to(self, lat, lng):
        if self.latitude is None or self.longitude is None:
            return None
        r = 6371.0
        dlat = math.radians(lat - self.latitude)
        dlng = math.radians(lng - self.longitude)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(self.latitude)) *
             math.cos(math.radians(lat)) *
             math.sin(dlng / 2) ** 2)
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # 🔥 NEW (online indicator logic)
    def is_user_online(self):
        return (timezone.now() - self.last_seen).seconds < 300


class PublicIdCounter(models.Model):
    class Kind(models.TextChoices):
        PLAYER = 'player', 'Player'
        VENUE_OWNER = 'venue_owner', 'Venue owner'

    kind = models.CharField(max_length=20, choices=Kind.choices, unique=True)
    next_value = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'my_app_public_id_counter'


# ---------------------------------------------------------------------------
# SPORT
# ---------------------------------------------------------------------------
class Sport(TimestampedPublicModel):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=60, unique=True, db_index=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True)
    category = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)[:60]
            candidate = base_slug
            sequence = 2
            while Sport.objects.exclude(pk=self.pk).filter(slug=candidate).exists():
                suffix = f'-{sequence}'
                candidate = f'{base_slug[:60 - len(suffix)]}{suffix}'
                sequence += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['name']


class SportFormat(TimestampedPublicModel):
    sport = models.ForeignKey(Sport, on_delete=models.CASCADE, related_name='formats')
    name = models.CharField(max_length=80)
    minimum_players = models.PositiveIntegerField()
    maximum_players = models.PositiveIntegerField()
    default_capacity = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['sport', 'name'], name='unique_sport_format_name'),
            models.CheckConstraint(check=models.Q(minimum_players__gt=0), name='sport_format_min_positive'),
            models.CheckConstraint(check=models.Q(minimum_players__lte=models.F('maximum_players')), name='sport_format_min_lte_max'),
            models.CheckConstraint(check=models.Q(default_capacity__gte=models.F('minimum_players'), default_capacity__lte=models.F('maximum_players')), name='sport_format_default_in_range'),
        ]


# ---------------------------------------------------------------------------
# PLAYER PROFILE
# ---------------------------------------------------------------------------
class PlayerProfile(TimestampedPublicModel):
    SKILL_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
        ('pro', 'Pro'),
    ]
    MOOD_CHOICES = [
        ('relaxed', 'Relaxed'),
        ('energetic', 'Energetic'),
        ('learning', 'Learning'),
        ('social', 'Social'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')

    # SportMate player preferences. Legacy statistics remain read-only for now.
    bio = models.TextField(blank=True)
    availability = models.JSONField(default=dict, blank=True)
    preferred_distance = models.PositiveIntegerField(default=15)
    preferred_play_times = models.JSONField(default=list, blank=True)
    matching_mood = models.CharField(max_length=16, choices=MOOD_CHOICES, blank=True, default='')
    profile_visibility = models.CharField(
        max_length=16,
        choices=[('public', 'Public'), ('sports_only', 'Sports only'), ('private', 'Private')],
        default='public',
    )

    skill_level = models.CharField(max_length=20, choices=SKILL_CHOICES, default='beginner')
    matches_played = models.PositiveIntegerField(default=0)
    matches_won = models.PositiveIntegerField(default=0)

    rating = models.FloatField(default=0.0)
    achievements = models.JSONField(default=list, blank=True)

    def win_rate(self):
        if self.matches_played == 0:
            return 0
        return (self.matches_won / self.matches_played) * 100

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(matches_won__lte=models.F('matches_played')),
                name='profile_wins_cannot_exceed_matches_played',
            ),
            models.CheckConstraint(
                check=models.Q(preferred_distance__gte=1, preferred_distance__lte=500),
                name='profile_preferred_distance_is_valid',
            ),
        ]


class UserSport(TimestampedPublicModel):
    class SkillLevel(models.TextChoices):
        BEGINNER = 'beginner', 'Beginner'
        INTERMEDIATE = 'intermediate', 'Intermediate'
        ADVANCED = 'advanced', 'Advanced'
        EXPERT = 'expert', 'Expert'
        PRO = 'pro', 'Pro'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='user_sports')
    sport = models.ForeignKey(Sport, on_delete=models.CASCADE, related_name='user_profiles')
    skill_level = models.CharField(max_length=16, choices=SkillLevel.choices, default=SkillLevel.BEGINNER)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0)
    matches_played = models.PositiveIntegerField(default=0)
    wins = models.PositiveIntegerField(default=0)
    losses = models.PositiveIntegerField(default=0)
    preferred = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'sport'], name='unique_user_sport'),
            models.CheckConstraint(check=models.Q(rating__gte=0, rating__lte=5), name='user_sport_rating_is_valid'),
            models.CheckConstraint(check=models.Q(wins__lte=models.F('matches_played')), name='user_sport_wins_valid'),
            models.CheckConstraint(check=models.Q(losses__lte=models.F('matches_played')), name='user_sport_losses_valid'),
            models.CheckConstraint(
                check=models.Q(wins__lte=models.F('matches_played') - models.F('losses')),
                name='user_sport_results_do_not_exceed_matches',
            ),
        ]
        indexes = [models.Index(fields=['sport', 'skill_level'], name='user_sport_skill_idx')]


class UserBlock(TimestampedPublicModel):
    """A directional block; blocked pairs never appear in recommendations."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='blocks_created')
    blocked_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='blocked_by')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'blocked_user'], name='unique_user_block'),
            models.CheckConstraint(check=~models.Q(user=models.F('blocked_user')), name='user_cannot_block_self'),
        ]
        indexes = [models.Index(fields=['user', 'blocked_user'], name='user_block_pair_idx')]


class Venue(TimestampedPublicModel):
    # A discovered Ground becomes playable only after staff/venue operators
    # create this verified venue record, set operating hours, and add courts.
    source_ground = models.OneToOneField(
        'Ground', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='verified_venue',
    )
    # Legacy tournament-location projection. New tournaments point directly
    # to Tournament.nearby_ground and do not create Venue rows from discovery.
    source_nearby_ground = models.OneToOneField(
        'SportsGround', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tournament_venue',
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='owned_venues')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100, db_index=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    opening_time = models.TimeField()
    closing_time = models.TimeField()
    timezone = models.CharField(max_length=64, default='Asia/Kolkata')
    active = models.BooleanField(default=True, db_index=True)
    is_demo = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [models.Index(fields=['city', 'active'], name='venue_city_active_idx')]
        constraints = [
            models.CheckConstraint(check=models.Q(latitude__gte=-90, latitude__lte=90), name='venue_latitude_valid'),
            models.CheckConstraint(check=models.Q(longitude__gte=-180, longitude__lte=180), name='venue_longitude_valid'),
            models.CheckConstraint(check=models.Q(rating__gte=0, rating__lte=5), name='venue_rating_valid'),
            models.CheckConstraint(check=models.Q(closing_time__gt=models.F('opening_time')), name='venue_hours_valid'),
        ]


class VenueSport(TimestampedPublicModel):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='venue_sports')
    sport = models.ForeignKey(Sport, on_delete=models.CASCADE, related_name='venue_offerings')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['venue', 'sport'], name='unique_venue_sport')]


class Court(TimestampedPublicModel):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='courts')
    sport = models.ForeignKey(Sport, on_delete=models.PROTECT, related_name='courts')
    name = models.CharField(max_length=100)
    capacity = models.PositiveIntegerField(default=2)
    price_per_hour = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['venue', 'name'], name='unique_court_name_per_venue'),
            models.CheckConstraint(check=models.Q(capacity__gt=0), name='court_capacity_valid'),
            models.CheckConstraint(check=models.Q(price_per_hour__gte=0), name='court_price_valid'),
        ]
        indexes = [models.Index(fields=['sport', 'active', 'price_per_hour'], name='court_discovery_idx')]


class VenueAmenity(TimestampedPublicModel):
    venue = models.OneToOneField(Venue, on_delete=models.CASCADE, related_name='amenities')
    parking = models.BooleanField(default=False)
    washroom = models.BooleanField(default=False)
    changing_room = models.BooleanField(default=False)
    drinking_water = models.BooleanField(default=False)
    equipment = models.BooleanField(default=False)
    lighting = models.BooleanField(default=False)
    air_conditioning = models.BooleanField(default=False)
    seating = models.BooleanField(default=False)


class VenueImage(TimestampedPublicModel):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='venues/')
    caption = models.CharField(max_length=150, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'created_at']


class CourtBooking(TimestampedPublicModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'
        CONFIRMED = 'confirmed', 'Confirmed'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='court_bookings')
    court = models.ForeignKey(Court, on_delete=models.PROTECT, related_name='bookings')
    booking_reference = models.CharField(max_length=16, unique=True, db_index=True, null=True, blank=True)
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name='bookings', null=True, blank=True)
    booking_date = models.DateField(null=True, blank=True, db_index=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    additional_fees = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CONFIRMED, db_index=True)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(ends_at__gt=models.F('starts_at')), name='booking_time_valid')]
        indexes = [models.Index(fields=['court', 'starts_at', 'status'], name='booking_court_time_idx')]
        indexes += [models.Index(fields=['user', 'status', 'booking_date'], name='booking_user_status_date_idx')]


class PaymentTransaction(TimestampedPublicModel):
    class Status(models.TextChoices):
        CREATED = 'created', 'Created'
        AUTHORIZED = 'authorized', 'Authorized'
        CAPTURED = 'captured', 'Captured'
        FAILED = 'failed', 'Failed'

    booking = models.OneToOneField(CourtBooking, on_delete=models.PROTECT, related_name='payment')
    provider = models.CharField(max_length=32, default='razorpay')
    provider_order_id = models.CharField(max_length=64, unique=True, db_index=True)
    provider_payment_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    amount_paise = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED, db_index=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'created_at'], name='payment_status_created_idx')]


class VenueCancellationPolicy(TimestampedPublicModel):
    venue = models.OneToOneField(Venue, on_delete=models.CASCADE, related_name='cancellation_policy')
    minimum_cancellation_hours = models.PositiveIntegerField(default=0)
    allow_cancellation = models.BooleanField(default=True)
    cancellation_window_description = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)


class CourtBlockedPeriod(TimestampedPublicModel):
    class Type(models.TextChoices):
        BLOCKED = 'blocked', 'Blocked'
        MAINTENANCE = 'maintenance', 'Maintenance'

    court = models.ForeignKey(Court, on_delete=models.CASCADE, related_name='blocked_periods')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.BLOCKED)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(ends_at__gt=models.F('starts_at')), name='blocked_period_time_valid')]
        indexes = [models.Index(fields=['court', 'starts_at', 'ends_at'], name='court_block_period_idx')]


class VenueReview(TimestampedPublicModel):
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='venue_reviews')
    booking = models.OneToOneField(CourtBooking, on_delete=models.PROTECT, related_name='review')
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)

    class Meta:
        constraints = [models.CheckConstraint(check=models.Q(rating__gte=1, rating__lte=5), name='review_rating_valid')]
        indexes = [models.Index(fields=['venue', 'created_at'], name='review_venue_created_idx')]


# ---------------------------------------------------------------------------
# 🔥 NEW: GROUNDS (IMPORTANT FOR YOUR UI)
# ---------------------------------------------------------------------------
class Ground(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    SIZE_CHOICES = [
        ('small', 'Small'),
        ('medium', 'Medium'),
        ('big', 'Big'),
    ]

    name = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)

    latitude = models.FloatField()
    longitude = models.FloatField()

    price_per_hour = models.FloatField(default=0)
    pitch_type = models.CharField(max_length=50, blank=True)
    size = models.CharField(max_length=10, choices=SIZE_CHOICES, default='medium')
    sports = models.ManyToManyField(Sport, related_name='grounds', blank=True)
    address = models.CharField(max_length=255, blank=True)
    maps_place_id = models.CharField(max_length=255, blank=True, unique=True, null=True)
    google_photo_name = models.CharField(max_length=512, blank=True)
    source = models.CharField(max_length=30, default='manual')

    def __str__(self):
        return self.name

    def distance_to(self, lat, lng):
        r = 6371.0
        dlat = math.radians(lat - self.latitude)
        dlng = math.radians(lng - self.longitude)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(self.latitude)) *
             math.cos(math.radians(lat)) *
             math.sin(dlng / 2) ** 2)
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    class Meta:
        indexes = [models.Index(fields=['city', 'name'], name='ground_city_name_idx')]
        constraints = [
            models.CheckConstraint(check=models.Q(price_per_hour__gte=0), name='ground_price_is_non_negative'),
            models.CheckConstraint(check=models.Q(latitude__gte=-90, latitude__lte=90), name='ground_latitude_is_valid'),
            models.CheckConstraint(check=models.Q(longitude__gte=-180, longitude__lte=180), name='ground_longitude_is_valid'),
        ]




class SportsGround(models.Model):
    """An OpenStreetMap sports feature cached for nearby-ground discovery."""

    osm_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    ground_type = models.CharField(max_length=100, db_index=True)
    sport = models.CharField(max_length=100, db_index=True)
    latitude = models.FloatField(db_index=True)
    longitude = models.FloatField(db_index=True)
    google_rating = models.FloatField(null=True, blank=True)
    google_rating_count = models.PositiveIntegerField(null=True, blank=True)
    google_photo_name = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['latitude', 'longitude'], name='sports_ground_coords_idx'),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# AVAILABILITY
# ---------------------------------------------------------------------------
class AvailabilitySlot(TimestampedPublicModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='availability_slots')

    day_of_week = models.IntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    label = models.CharField(max_length=50, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(day_of_week__gte=0, day_of_week__lte=6), name='availability_day_is_valid'),
            models.CheckConstraint(check=models.Q(end_time__gt=models.F('start_time')), name='availability_end_after_start'),
            models.UniqueConstraint(fields=['user', 'day_of_week', 'start_time', 'end_time'], name='unique_availability_slot'),
        ]
        indexes = [models.Index(fields=['user', 'day_of_week', 'start_time'], name='availability_user_time_idx')]


# ---------------------------------------------------------------------------
# MATCH (UPDATED 🔥)
# ---------------------------------------------------------------------------
class Match(TimestampedPublicModel):
    STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('ongoing', 'Ongoing'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    sport = models.ForeignKey(Sport, on_delete=models.CASCADE)

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='organized_matches'
    )

    # 🔥 NEW (linked to ground)
    ground = models.ForeignKey(Ground, on_delete=models.CASCADE)

    date_time = models.DateTimeField()
    end_date_time = models.DateTimeField()
    title = models.CharField(max_length=200, blank=True, default='')
    description = models.TextField(blank=True, default='')
    total_players = models.IntegerField()

    joined_players = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='joined_matches',
        blank=True
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')

    def save(self, *args, **kwargs):
        # Keep existing integrations working while all new matches submit an
        # explicit end time through MatchCreateSerializer.
        if self.end_date_time is None and self.date_time:
            self.end_date_time = self.date_time + timedelta(hours=1)
        super().save(*args, **kwargs)

    def joined_count(self):
        return self.joined_players.count()

    class Meta:
        indexes = [
            models.Index(fields=['status', 'date_time'], name='match_status_date_idx'),
            models.Index(fields=['sport', 'date_time'], name='match_sport_date_idx'),
            models.Index(fields=['ground', 'date_time'], name='match_ground_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(total_players__gt=0), name='match_total_players_is_positive'),
            models.CheckConstraint(check=models.Q(end_date_time__gt=models.F('date_time')), name='match_end_after_start'),
        ]


class MatchParticipation(TimestampedPublicModel):
    """Auditable legacy Match membership; active roster remains the M2M field."""
    class Status(models.TextChoices):
        JOINED = 'joined', 'Joined'
        LEFT = 'left', 'Left'
        REMOVED = 'removed', 'Removed'

    class Attendance(models.TextChoices):
        PENDING = 'pending', 'Attendance pending'
        ATTENDED = 'attended', 'Attended'
        NO_SHOW = 'no_show', 'No-show'

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='participation_history')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='match_participation_history')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.JOINED, db_index=True)
    joined_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=500, blank=True)
    attendance = models.CharField(max_length=12, choices=Attendance.choices, default=Attendance.PENDING, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['match', 'user'], condition=models.Q(status='joined'), name='unique_active_match_participation')]
        indexes = [models.Index(fields=['user', 'status'], name='match_participant_user_idx')]


class MatchTimePoll(TimestampedPublicModel):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        TIE = 'tie', 'Tie - organizer decision required'
        FINALIZED = 'finalized', 'Finalized'
        CLOSED = 'closed', 'Closed'

    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='time_polls')
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_match_time_polls')
    deadline = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    finalized_option = models.ForeignKey('MatchTimePollOption', null=True, blank=True, on_delete=models.PROTECT, related_name='finalized_polls')
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='finalized_match_time_polls')
    finalized_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        indexes = [models.Index(fields=['match', 'status'], name='match_timepoll_status_idx')]


class MatchTimePollOption(TimestampedPublicModel):
    poll = models.ForeignKey(MatchTimePoll, on_delete=models.CASCADE, related_name='options')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['poll', 'starts_at'], name='unique_match_poll_option_start'),
            models.CheckConstraint(check=models.Q(ends_at__gt=models.F('starts_at')), name='match_poll_option_end_after_start'),
        ]
        ordering = ['starts_at', 'id']


class MatchTimePollVoter(TimestampedPublicModel):
    poll = models.ForeignKey(MatchTimePoll, on_delete=models.CASCADE, related_name='eligible_voters')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='eligible_match_time_polls')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['poll', 'user'], name='unique_match_poll_voter')]


class MatchTimePollVote(TimestampedPublicModel):
    poll = models.ForeignKey(MatchTimePoll, on_delete=models.CASCADE, related_name='votes')
    voter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='match_time_poll_votes')
    option = models.ForeignKey(MatchTimePollOption, on_delete=models.CASCADE, related_name='votes')
    is_active = models.BooleanField(default=True, db_index=True)
    changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['poll', 'voter'], condition=models.Q(is_active=True), name='unique_active_match_poll_vote'),
        ]
        indexes = [models.Index(fields=['poll', 'is_active'], name='match_poll_vote_count_idx')]


class MatchReplacementOpportunity(TimestampedPublicModel):
    """A time-limited request to refill a slot opened in an upcoming match."""
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='replacement_opportunities')
    withdrawn_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='opened_match_rescues')
    expires_at = models.DateTimeField(db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    replacement_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='accepted_match_rescues')

    class Meta:
        indexes = [models.Index(fields=['match', 'closed_at', 'expires_at'], name='match_rescue_open_idx')]


class MatchReplacementInvitation(TimestampedPublicModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'
        EXPIRED = 'expired', 'Expired'
        UNAVAILABLE = 'unavailable', 'Unavailable'

    opportunity = models.ForeignKey(MatchReplacementOpportunity, on_delete=models.CASCADE, related_name='invitations')
    candidate = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='match_replacement_invitations')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity', 'candidate'], name='unique_match_rescue_candidate')]
        indexes = [models.Index(fields=['candidate', 'status', 'created_at'], name='rescue_candidate_state_idx')]


class MatchReplacementHistory(TimestampedPublicModel):
    opportunity = models.OneToOneField(MatchReplacementOpportunity, on_delete=models.PROTECT, related_name='history')
    match = models.ForeignKey(Match, on_delete=models.PROTECT, related_name='replacement_history')
    withdrawn_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='match_replacement_history_left')
    replacement_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='match_replacement_history_joined')


# ---------------------------------------------------------------------------
# GAMES
# ---------------------------------------------------------------------------
# `Match` above is the legacy ground-based feature retained for existing
# clients.  Games are the court-backed, capacity-aware activity model.
class Game(TimestampedPublicModel):
    class Visibility(models.TextChoices):
        PUBLIC = 'public', 'Public'
        PRIVATE = 'private', 'Private'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        ALMOST_FULL = 'almost_full', 'Almost full'
        FULL = 'full', 'Full'
        STARTED = 'started', 'Started'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    game_reference = models.CharField(max_length=20, unique=True, db_index=True)
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='hosted_games')
    sport = models.ForeignKey(Sport, on_delete=models.PROTECT, related_name='games')
    venue = models.ForeignKey(Venue, on_delete=models.PROTECT, related_name='games')
    court = models.ForeignKey(Court, on_delete=models.PROTECT, related_name='games')
    game_date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    # UTC instants used for efficient, timezone-safe overlap checks.
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    min_players = models.PositiveIntegerField(default=2)
    max_players = models.PositiveIntegerField()
    skill_level = models.CharField(max_length=16, choices=UserSport.SkillLevel.choices, blank=True)
    description = models.TextField(blank=True)
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(end_time__gt=models.F('start_time')), name='game_end_after_start'),
            models.CheckConstraint(check=models.Q(min_players__gt=0), name='game_min_players_positive'),
            models.CheckConstraint(check=models.Q(max_players__gt=0), name='game_max_players_positive'),
            models.CheckConstraint(check=models.Q(min_players__lte=models.F('max_players')), name='game_min_lte_max'),
            models.CheckConstraint(check=models.Q(ends_at__gt=models.F('starts_at')), name='game_datetime_range_valid'),
        ]
        indexes = [
            models.Index(fields=['sport', 'game_date', 'status'], name='game_sport_date_status_idx'),
            models.Index(fields=['venue', 'game_date'], name='game_venue_date_idx'),
            models.Index(fields=['court', 'starts_at', 'status'], name='game_court_time_status_idx'),
            models.Index(fields=['host', 'status'], name='game_host_status_idx'),
        ]


class GamePlayer(TimestampedPublicModel):
    class Status(models.TextChoices):
        CONFIRMED = 'confirmed', 'Confirmed'
        LEFT = 'left', 'Left'
        REMOVED = 'removed', 'Removed'

    class Attendance(models.TextChoices):
        PENDING = 'pending', 'Attendance pending'
        ATTENDED = 'attended', 'Attended'
        NO_SHOW = 'no_show', 'No-show'

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='game_players')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='game_participations')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.CONFIRMED, db_index=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    attendance = models.CharField(max_length=12, choices=Attendance.choices, default=Attendance.PENDING, db_index=True)

    class Meta:
        constraints = [
            # A partial uniqueness constraint permits historical left/removed
            # rows while making concurrent active joins impossible.
            models.UniqueConstraint(
                fields=['game', 'user'], condition=models.Q(status='confirmed'),
                name='unique_active_game_membership',
            ),
        ]
        indexes = [
            models.Index(fields=['game', 'status'], name='game_player_game_status_idx'),
            models.Index(fields=['user', 'status'], name='game_player_user_status_idx'),
        ]


# ---------------------------------------------------------------------------
# COMMUNITIES
# ---------------------------------------------------------------------------
class Community(TimestampedPublicModel):
    class Visibility(models.TextChoices):
        PUBLIC = 'public', 'Public'
        PRIVATE = 'private', 'Private'

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True, db_index=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_communities')
    sport = models.ForeignKey(Sport, on_delete=models.PROTECT, related_name='communities', null=True, blank=True)
    city = models.CharField(max_length=100, blank=True, db_index=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC, db_index=True)
    avatar = models.ImageField(upload_to='communities/avatars/', null=True, blank=True)
    cover_image = models.ImageField(upload_to='communities/covers/', null=True, blank=True)
    is_club = models.BooleanField(default=False, db_index=True)
    membership_open = models.BooleanField(default=True)
    college = models.ForeignKey('College', null=True, blank=True, on_delete=models.SET_NULL, related_name='clubs')
    member_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['sport', 'city', 'is_active'], name='community_discovery_idx'),
            models.Index(fields=['owner', 'is_active'], name='community_owner_active_idx'),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(latitude__isnull=True) | models.Q(latitude__gte=-90, latitude__lte=90), name='community_latitude_valid'),
            models.CheckConstraint(check=models.Q(longitude__isnull=True) | models.Q(longitude__gte=-180, longitude__lte=180), name='community_longitude_valid'),
        ]


class CommunityMember(TimestampedPublicModel):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Owner'
        CAPTAIN = 'captain', 'Captain'
        ADMIN = 'admin', 'Admin'
        MODERATOR = 'moderator', 'Moderator'
        MEMBER = 'member', 'Member'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PENDING = 'pending', 'Pending'
        BANNED = 'banned', 'Banned'
        LEFT = 'left', 'Left'

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='community_memberships')
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.MEMBER, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['community', 'user'], name='unique_community_member')]
        indexes = [
            models.Index(fields=['community', 'status', 'role'], name='community_member_status_idx'),
            models.Index(fields=['user', 'status'], name='community_user_status_idx'),
        ]


class CommunityPost(TimestampedPublicModel):
    class Type(models.TextChoices):
        TEXT = 'text', 'Text'
        GAME = 'game', 'Game'
        EVENT = 'event', 'Event'
        ANNOUNCEMENT = 'announcement', 'Announcement'

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='posts')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='community_posts')
    game = models.ForeignKey(Game, null=True, blank=True, on_delete=models.SET_NULL, related_name='community_posts')
    content = models.TextField(blank=True)
    post_type = models.CharField(max_length=16, choices=Type.choices, default=Type.TEXT, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['community', 'created_at'], name='community_post_created_idx')]


class CommunityComment(TimestampedPublicModel):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='community_comments')
    content = models.TextField(blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['post', 'created_at'], name='community_comment_created_idx')]


class CommunityPostLike(TimestampedPublicModel):
    post = models.ForeignKey(CommunityPost, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='community_post_likes')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['post', 'user'], name='unique_community_post_like')]
        indexes = [models.Index(fields=['post', 'created_at'], name='community_like_post_idx')]


class College(TimestampedPublicModel):
    """A college/institution whose official status is explicitly reviewed."""
    class VerificationStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        VERIFIED = 'verified', 'Verified'
        REJECTED = 'rejected', 'Rejected'

    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    city = models.CharField(max_length=100, db_index=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default='India')
    description = models.TextField(blank=True)
    approved_email_domain = models.CharField(max_length=255, blank=True)
    verification_evidence = models.URLField(max_length=500, blank=True)
    verification_status = models.CharField(max_length=12, choices=VerificationStatus.choices, default=VerificationStatus.PENDING, db_index=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='college_submissions')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='college_reviews')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=500, blank=True)


class CollegeMembership(TimestampedPublicModel):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'College administrator'
        PLAYER = 'player', 'Player'
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACTIVE = 'active', 'Active'
        REJECTED = 'rejected', 'Rejected'
        LEFT = 'left', 'Left'

    college = models.ForeignKey(College, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='college_memberships')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.PLAYER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['college', 'user'], name='unique_college_membership')]


class CollegeChallenge(TimestampedPublicModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'
        CANCELLED = 'cancelled', 'Cancelled'

    challenger = models.ForeignKey(College, on_delete=models.PROTECT, related_name='challenges_sent')
    challenged = models.ForeignKey(College, on_delete=models.PROTECT, related_name='challenges_received')
    sport = models.ForeignKey(Sport, on_delete=models.PROTECT, related_name='college_challenges')
    message = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    tournament = models.OneToOneField('Tournament', null=True, blank=True, on_delete=models.SET_NULL, related_name='college_challenge')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_college_challenges')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['challenger', 'challenged', 'sport'], condition=models.Q(status='pending'), name='unique_pending_college_challenge')]


class CollegeResultDispute(TimestampedPublicModel):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        RESOLVED = 'resolved', 'Resolved'
        DISMISSED = 'dismissed', 'Dismissed'

    match = models.ForeignKey('TournamentMatch', on_delete=models.CASCADE, related_name='college_disputes')
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='college_result_disputes')
    reason = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True)
    resolution = models.TextField(blank=True, max_length=2000)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='resolved_college_disputes')
    resolved_at = models.DateTimeField(null=True, blank=True)


# ---------------------------------------------------------------------------
# EVENTS & TOURNAMENTS
# ---------------------------------------------------------------------------
class Event(TimestampedPublicModel):
    class Visibility(models.TextChoices): PUBLIC='public','Public'; PRIVATE='private','Private'; COMMUNITY='community','Community'
    class Type(models.TextChoices): MEETUP='meetup','Meetup'; COMPETITION='competition','Competition'; TOURNAMENT='tournament','Tournament'; WORKSHOP='workshop','Workshop'; OTHER='other','Other'
    class Status(models.TextChoices): DRAFT='draft','Draft'; OPEN='open','Open'; FULL='full','Full'; STARTED='started','Started'; COMPLETED='completed','Completed'; CANCELLED='cancelled','Cancelled'
    event_reference=models.CharField(max_length=20,unique=True,db_index=True)
    title=models.CharField(max_length=200); description=models.TextField(blank=True)
    organizer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='organized_events')
    sport=models.ForeignKey(Sport,on_delete=models.PROTECT,related_name='events')
    community=models.ForeignKey(Community,null=True,blank=True,on_delete=models.SET_NULL,related_name='events')
    venue=models.ForeignKey(Venue,on_delete=models.PROTECT,related_name='events')
    court=models.ForeignKey(Court,null=True,blank=True,on_delete=models.PROTECT,related_name='events')
    game=models.ForeignKey(Game,null=True,blank=True,on_delete=models.SET_NULL,related_name='events')
    event_date=models.DateField(db_index=True); start_time=models.TimeField(); end_time=models.TimeField(); starts_at=models.DateTimeField(db_index=True); ends_at=models.DateTimeField()
    maximum_participants=models.PositiveIntegerField(); minimum_participants=models.PositiveIntegerField(default=1)
    skill_level=models.CharField(max_length=16,choices=UserSport.SkillLevel.choices,blank=True)
    visibility=models.CharField(max_length=12,choices=Visibility.choices,default=Visibility.PUBLIC,db_index=True)
    event_type=models.CharField(max_length=16,choices=Type.choices,default=Type.MEETUP)
    status=models.CharField(max_length=12,choices=Status.choices,default=Status.OPEN,db_index=True)
    registration_deadline=models.DateTimeField()
    image=models.ImageField(upload_to='events/',null=True,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(check=models.Q(end_time__gt=models.F('start_time')),name='event_end_after_start'),models.CheckConstraint(check=models.Q(minimum_participants__gt=0),name='event_min_positive'),models.CheckConstraint(check=models.Q(minimum_participants__lte=models.F('maximum_participants')),name='event_min_lte_max'),models.CheckConstraint(check=models.Q(ends_at__gt=models.F('starts_at')),name='event_range_valid')]
        indexes=[models.Index(fields=['sport','event_date','status'],name='event_sport_date_status_idx'),models.Index(fields=['venue','event_date'],name='event_venue_date_idx'),models.Index(fields=['organizer','status'],name='event_organizer_status_idx')]


class EventParticipant(TimestampedPublicModel):
    class Status(models.TextChoices): REGISTERED='registered','Registered'; WAITLISTED='waitlisted','Waitlisted'; CANCELLED='cancelled','Cancelled'; REMOVED='removed','Removed'
    event=models.ForeignKey(Event,on_delete=models.CASCADE,related_name='participants'); user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='event_participations')
    status=models.CharField(max_length=12,choices=Status.choices,default=Status.REGISTERED,db_index=True); joined_at=models.DateTimeField(auto_now_add=True); left_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['event','user'],name='unique_event_participant')]
        indexes=[models.Index(fields=['event','status','joined_at'],name='event_participant_waitlist_idx'),models.Index(fields=['user','status'],name='event_participant_user_idx')]


class EventReminder(TimestampedPublicModel):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='reminders')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='event_reminders')
    remind_at = models.DateTimeField(db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'user'], name='unique_event_reminder')]
        indexes = [models.Index(fields=['sent_at', 'cancelled_at', 'remind_at'], name='event_reminder_due_idx')]


class Tournament(TimestampedPublicModel):
    class Format(models.TextChoices): SINGLE_ELIMINATION='single_elimination','Single elimination'; DOUBLE_ELIMINATION='double_elimination','Double elimination'; ROUND_ROBIN='round_robin','Round robin'; LEAGUE='league','League'
    class Status(models.TextChoices): DRAFT='draft','Draft'; OPEN='open','Open'; FULL='full','Full'; STARTED='started','Started'; COMPLETED='completed','Completed'; CANCELLED='cancelled','Cancelled'
    tournament_reference=models.CharField(max_length=20,unique=True,db_index=True); name=models.CharField(max_length=200)
    is_inter_college = models.BooleanField(default=False, db_index=True)
    organizer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='organized_tournaments'); sport=models.ForeignKey(Sport,on_delete=models.PROTECT,related_name='tournaments')
    community=models.ForeignKey(Community,null=True,blank=True,on_delete=models.SET_NULL,related_name='tournaments'); venue=models.ForeignKey(Venue,null=True,blank=True,on_delete=models.PROTECT,related_name='tournaments'); nearby_ground=models.ForeignKey(SportsGround,null=True,blank=True,on_delete=models.SET_NULL,related_name='tournaments'); event=models.OneToOneField(Event,null=True,blank=True,on_delete=models.SET_NULL,related_name='tournament')
    start_date=models.DateField(); end_date=models.DateField(); registration_deadline=models.DateTimeField(); maximum_teams=models.PositiveIntegerField(); format=models.CharField(max_length=24,choices=Format.choices,default=Format.SINGLE_ELIMINATION); skill_level=models.CharField(max_length=16,choices=UserSport.SkillLevel.choices,blank=True); status=models.CharField(max_length=12,choices=Status.choices,default=Status.OPEN,db_index=True)
    class Meta:
        constraints=[models.CheckConstraint(check=models.Q(end_date__gte=models.F('start_date')),name='tournament_date_range_valid'),models.CheckConstraint(check=models.Q(maximum_teams__gt=1),name='tournament_max_teams_valid')]
        indexes=[models.Index(fields=['sport','start_date','status'],name='tournament_sport_date_idx')]


class TournamentTeam(TimestampedPublicModel):
    class Status(models.TextChoices): ACTIVE='active','Active'; ELIMINATED='eliminated','Eliminated'; WITHDRAWN='withdrawn','Withdrawn'; CHAMPION='champion','Champion'
    tournament=models.ForeignKey(Tournament,on_delete=models.CASCADE,related_name='teams'); college=models.ForeignKey('College',null=True,blank=True,on_delete=models.PROTECT,related_name='tournament_teams'); name=models.CharField(max_length=120); logo_url=models.URLField(max_length=500,blank=True); description=models.TextField(blank=True); captain=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='captained_tournament_teams'); created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='created_tournament_teams'); seed=models.PositiveIntegerField(null=True,blank=True); status=models.CharField(max_length=12,choices=Status.choices,default=Status.ACTIVE)
    class Meta: constraints=[models.UniqueConstraint(fields=['tournament','name'],name='unique_tournament_team_name')]


class TournamentTeamMember(TimestampedPublicModel):
    class Role(models.TextChoices): CAPTAIN='captain','Captain'; PLAYER='player','Player'
    class Status(models.TextChoices): ACTIVE='active','Active'; LEFT='left','Left'; REMOVED='removed','Removed'
    team=models.ForeignKey(TournamentTeam,on_delete=models.CASCADE,related_name='members'); user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='tournament_team_memberships'); role=models.CharField(max_length=10,choices=Role.choices,default=Role.PLAYER); status=models.CharField(max_length=10,choices=Status.choices,default=Status.ACTIVE); joined_at=models.DateTimeField(auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['team','user'],name='unique_tournament_team_member')]


class TournamentMatch(TimestampedPublicModel):
    class Status(models.TextChoices): SCHEDULED='scheduled','Scheduled'; LIVE='live','Live'; COMPLETED='completed','Completed'; CANCELLED='cancelled','Cancelled'
    tournament=models.ForeignKey(Tournament,on_delete=models.CASCADE,related_name='matches'); round_number=models.PositiveIntegerField(); match_number=models.PositiveIntegerField(); team_a=models.ForeignKey(TournamentTeam,null=True,blank=True,on_delete=models.SET_NULL,related_name='home_matches'); team_b=models.ForeignKey(TournamentTeam,null=True,blank=True,on_delete=models.SET_NULL,related_name='away_matches'); venue=models.ForeignKey(Venue,null=True,blank=True,on_delete=models.SET_NULL); court=models.ForeignKey(Court,null=True,blank=True,on_delete=models.SET_NULL); scheduled_at=models.DateTimeField(null=True,blank=True); score_a=models.IntegerField(null=True,blank=True); score_b=models.IntegerField(null=True,blank=True); winner=models.ForeignKey(TournamentTeam,null=True,blank=True,on_delete=models.SET_NULL,related_name='won_tournament_matches'); status=models.CharField(max_length=12,choices=Status.choices,default=Status.SCHEDULED)
    class Meta: constraints=[models.UniqueConstraint(fields=['tournament','round_number','match_number'],name='unique_tournament_match_position')]; indexes=[models.Index(fields=['tournament','round_number','status'],name='tournament_match_round_idx')]


# ---------------------------------------------------------------------------
# MESSAGE (CHAT)
# ---------------------------------------------------------------------------
class Conversation(TimestampedPublicModel):
    class Type(models.TextChoices):
        ONE_TO_ONE = 'one_to_one', 'One to one'
        GAME_GROUP = 'game_group', 'Game group'

    conversation_type = models.CharField(max_length=16, choices=Type.choices, db_index=True)
    # One-to-one participants are persisted in canonical primary-key order.
    participant_one = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name='conversations_as_first_participant',
    )
    participant_two = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
        related_name='conversations_as_second_participant',
    )
    game = models.OneToOneField(Game, null=True, blank=True, on_delete=models.CASCADE, related_name='conversation')
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['participant_one', 'participant_two'],
                condition=models.Q(conversation_type='one_to_one', active=True),
                name='unique_active_direct_conversation',
            ),
            models.CheckConstraint(
                check=~models.Q(participant_one=models.F('participant_two')),
                name='conversation_participants_differ',
            ),
        ]
        indexes = [models.Index(fields=['conversation_type', 'active'], name='conversation_type_active_idx')]


class ConversationMember(TimestampedPublicModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='conversation_memberships')
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['conversation', 'user'], condition=models.Q(is_active=True),
                name='unique_active_conversation_member',
            ),
        ]
        indexes = [
            models.Index(fields=['conversation', 'is_active'], name='conversation_member_active_idx'),
            models.Index(fields=['user', 'is_active'], name='conversation_user_active_idx'),
        ]


class Message(TimestampedPublicModel):
    class Type(models.TextChoices):
        TEXT = 'text', 'Text'
        SYSTEM = 'system', 'System'

    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_messages')
    client_message_id = models.UUIDField(null=True, blank=True)
    # Retained for legacy `/messages/` clients. All conversation API messages
    # leave this null and use ConversationMember access control.
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='received_messages')

    message_type = models.CharField(max_length=10, choices=Type.choices, default=Type.TEXT, db_index=True)
    content = models.TextField(blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    is_edited = models.BooleanField(default=False)

    is_read = models.BooleanField(default=False)
    is_delivered = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['receiver', 'is_read', 'created_at'], name='message_receiver_read_idx'),
            models.Index(fields=['conversation', 'created_at'], name='message_conversation_time_idx'),
            models.Index(fields=['sender', 'created_at'], name='message_sender_time_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(receiver__isnull=True) | ~models.Q(sender=models.F('receiver')),
                name='message_sender_differs_from_receiver',
            ),
            models.UniqueConstraint(
                fields=['conversation', 'sender', 'client_message_id'],
                condition=models.Q(client_message_id__isnull=False),
                name='unique_conversation_client_message',
            ),
        ]


# ---------------------------------------------------------------------------
# NOTIFICATION (UPDATED 🔥)
# ---------------------------------------------------------------------------
class Notification(TimestampedPublicModel):
    TYPES = [
        ('match_invite', 'Match Invite'),
        ('message', 'Message'),
        ('reminder', 'Reminder'),
        ('ai_suggestion', 'AI Suggestion'),
        ('moderation_warning', 'Moderation Warning'),
        ('moderation_outcome', 'Moderation Outcome'),
        ('report_resolved', 'Report Resolved'),
        ('referral', 'Referral'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='acted_notifications')

    type = models.CharField(max_length=20, choices=TYPES)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    priority = models.IntegerField(default=1)
    event_key = models.CharField(max_length=160, blank=True, db_index=True)

    data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'is_read', 'created_at'], name='notif_user_read_time_idx'),
            models.Index(fields=['user', 'type', 'created_at'], name='notification_user_type_idx'),
            models.Index(fields=['user', 'deleted_at', '-created_at'], name='notif_user_del_created_idx'),
            models.Index(fields=['user', 'is_read', 'deleted_at'], name='notif_user_unread_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'event_key'],
                condition=~models.Q(event_key=''),
                name='notification_user_event_key_unique',
            ),
        ]


class NotificationPreference(TimestampedPublicModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_preferences')
    game_notifications = models.BooleanField(default=True)
    booking_notifications = models.BooleanField(default=True)
    chat_notifications = models.BooleanField(default=True)
    community_notifications = models.BooleanField(default=True)
    event_notifications = models.BooleanField(default=True)
    tournament_notifications = models.BooleanField(default=True)
    marketing_notifications = models.BooleanField(default=False)


class UserDevice(TimestampedPublicModel):
    class Platform(models.TextChoices): ANDROID='android','Android'; IOS='ios','iOS'; WEB='web','Web'
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='devices')
    device_token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    device_name = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'is_active'], name='device_user_active_idx')]


class PlayerRating(TimestampedPublicModel):
    reviewer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='ratings_given')
    player=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='ratings_received')
    game=models.ForeignKey(Game,null=True,blank=True,on_delete=models.CASCADE,related_name='player_ratings')
    # `rating` remains the stored overall score for backwards compatibility.
    # It is calculated from the four feedback categories by RatingService.
    rating=models.DecimalField(max_digits=3, decimal_places=1)
    playing_performance=models.PositiveSmallIntegerField()
    teamwork=models.PositiveSmallIntegerField()
    sportsmanship=models.PositiveSmallIntegerField()
    punctuality=models.PositiveSmallIntegerField()
    review=models.TextField(blank=True); deleted_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(check=models.Q(rating__gte=1,rating__lte=5),name='player_rating_range_valid'),models.CheckConstraint(check=models.Q(playing_performance__gte=1,playing_performance__lte=5),name='rating_performance_range_valid'),models.CheckConstraint(check=models.Q(teamwork__gte=1,teamwork__lte=5),name='rating_teamwork_range_valid'),models.CheckConstraint(check=models.Q(sportsmanship__gte=1,sportsmanship__lte=5),name='rating_sportsmanship_range_valid'),models.CheckConstraint(check=models.Q(punctuality__gte=1,punctuality__lte=5),name='rating_punctuality_range_valid'),models.UniqueConstraint(fields=['reviewer','player','game'],name='unique_player_game_rating')]
        indexes=[models.Index(fields=['player','created_at'],name='player_rating_created_idx')]


class MatchReflection(TimestampedPublicModel):
    """Private post-game self-reflection; intentionally separate from ratings."""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='reflections')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='match_reflections')
    enjoyment = models.PositiveSmallIntegerField()
    teamwork = models.PositiveSmallIntegerField()
    improvement = models.PositiveSmallIntegerField()
    note = models.CharField(max_length=280, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['game', 'user'], name='unique_game_user_reflection'),
            models.CheckConstraint(check=models.Q(enjoyment__gte=1, enjoyment__lte=5), name='reflection_enjoyment_valid'),
            models.CheckConstraint(check=models.Q(teamwork__gte=1, teamwork__lte=5), name='reflection_teamwork_valid'),
            models.CheckConstraint(check=models.Q(improvement__gte=1, improvement__lte=5), name='reflection_improvement_valid'),
        ]
        indexes = [models.Index(fields=['user', '-created_at'], name='reflection_user_created_idx')]


class ReferralAttribution(TimestampedPublicModel):
    """Private attribution for a qualifying new player registration."""
    class Status(models.TextChoices):
        SUCCESSFUL = 'successful', 'Successful'

    referrer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='referrals_made')
    referred_user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='referral_attribution')
    referral_code = models.CharField(max_length=20)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUCCESSFUL, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=~models.Q(referrer=models.F('referred_user')), name='referral_no_self_attribution'),
        ]
        indexes = [models.Index(fields=['referrer', 'status', '-created_at'], name='referrer_status_created_idx')]


class PlayerSportStatistics(TimestampedPublicModel):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='sport_statistics')
    sport=models.ForeignKey(Sport,on_delete=models.CASCADE,related_name='player_statistics')
    statistics=models.JSONField(default=dict,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['user','sport'],name='unique_player_sport_statistics')]


class Achievement(TimestampedPublicModel):
    class Category(models.TextChoices): GAMES='games','Games'; WINS='wins','Wins'; EVENTS='events','Events'; TOURNAMENTS='tournaments','Tournaments'; COMMUNITIES='communities','Communities'; SOCIAL='social','Social'; RATINGS='ratings','Ratings'; ACTIVITY='activity','Activity'
    code=models.CharField(max_length=64,unique=True,db_index=True); name=models.CharField(max_length=120); description=models.TextField(); category=models.CharField(max_length=16,choices=Category.choices); icon=models.CharField(max_length=120,blank=True); target_value=models.PositiveIntegerField(default=1); is_active=models.BooleanField(default=True)


class UserAchievement(TimestampedPublicModel):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='achievements')
    achievement=models.ForeignKey(Achievement,on_delete=models.CASCADE,related_name='user_achievements')
    progress=models.PositiveIntegerField(default=0); unlocked=models.BooleanField(default=False); unlocked_at=models.DateTimeField(null=True,blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['user','achievement'],name='unique_user_achievement')]


class SportChallenge(TimestampedPublicModel):
    """Time-bounded arena rules; all score values are calculated from server records."""
    class Kind(models.TextChoices):
        INDIVIDUAL = 'individual', 'Individual'
        TEAM = 'team', 'Team'

    class Metric(models.TextChoices):
        MATCHES_COMPLETED = 'matches_completed', 'Completed matches'
        PRACTICE_DAYS = 'practice_days', 'Practice days'
        CHECKLIST_COMPLETIONS = 'checklist_completions', 'Checklist completions'
        TOURNAMENT_WINS = 'tournament_wins', 'Tournament match wins'
        CALCULATED_GOALS_COMPLETED = 'calculated_goals_completed', 'Verified goals completed'

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    sport = models.ForeignKey(Sport, null=True, blank=True, on_delete=models.PROTECT, related_name='challenges')
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.INDIVIDUAL, db_index=True)
    metric = models.CharField(max_length=32, choices=Metric.choices, db_index=True)
    target = models.PositiveIntegerField()
    eligibility_min_skill = models.CharField(max_length=16, choices=UserSport.SkillLevel.choices, blank=True)
    starts_on = models.DateField(db_index=True)
    ends_on = models.DateField(db_index=True)
    reward_points = models.PositiveIntegerField(default=0)
    reward_achievement = models.ForeignKey(Achievement, null=True, blank=True, on_delete=models.SET_NULL, related_name='challenges')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_challenges')
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ['starts_on', 'name']
        constraints = [
            models.CheckConstraint(check=models.Q(target__gt=0), name='sport_challenge_target_positive'),
            models.CheckConstraint(check=models.Q(ends_on__gte=models.F('starts_on')), name='sport_challenge_date_range_valid'),
            models.CheckConstraint(check=models.Q(reward_points__lte=1000), name='sport_challenge_points_capped'),
        ]


class ChallengeEntry(TimestampedPublicModel):
    """One individual or club team entry. Progress and points are always derived."""
    challenge = models.ForeignKey(SportChallenge, on_delete=models.CASCADE, related_name='entries')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name='challenge_entries')
    team = models.ForeignKey('my_app.Community', null=True, blank=True, on_delete=models.CASCADE, related_name='challenge_entries')
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(models.Q(user__isnull=False, team__isnull=True) | models.Q(user__isnull=True, team__isnull=False)),
                name='challenge_entry_has_user_or_team',
            ),
            models.UniqueConstraint(fields=['challenge', 'user'], condition=models.Q(user__isnull=False), name='unique_challenge_user_entry'),
            models.UniqueConstraint(fields=['challenge', 'team'], condition=models.Q(team__isnull=False), name='unique_challenge_team_entry'),
        ]
        indexes = [models.Index(fields=['challenge', 'left_at'], name='challenge_entry_active_idx')]


class ChallengeEntryMember(TimestampedPublicModel):
    """Eligible club roster frozen when the team first enters a challenge."""
    entry = models.ForeignKey(ChallengeEntry, on_delete=models.CASCADE, related_name='roster')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='challenge_team_entries')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['entry', 'user'], name='unique_challenge_entry_member')]


class ChallengeRewardGrant(TimestampedPublicModel):
    """Idempotent record of a configured challenge reward for one entrant."""
    entry = models.ForeignKey(ChallengeEntry, on_delete=models.CASCADE, related_name='reward_grants')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='challenge_reward_grants')
    points = models.PositiveIntegerField(default=0)
    granted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['entry', 'user'], name='unique_challenge_reward_grant')]

class RecommendationProfile(TimestampedPublicModel):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='recommendation_profile')
    preferences=models.JSONField(default=dict,blank=True)
    last_evaluated_at=models.DateTimeField(null=True,blank=True)


class PracticeActivity(TimestampedPublicModel):
    """A user's reusable checklist item; completion rows retain their snapshots."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='practice_activities')
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=240, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [models.UniqueConstraint(fields=['user', 'name'], name='unique_practice_activity_name')]


class PracticeCompletion(TimestampedPublicModel):
    """Immutable daily completion event, including an activity-name snapshot."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='practice_completions')
    activity = models.ForeignKey(PracticeActivity, null=True, on_delete=models.SET_NULL, related_name='completions')
    activity_name = models.CharField(max_length=100)
    practice_date = models.DateField(db_index=True)
    completed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'practice_date', 'activity'], name='unique_daily_practice_completion')]
        indexes = [models.Index(fields=['user', 'practice_date'], name='practice_user_date_idx')]


class SportsGoal(TimestampedPublicModel):
    class Source(models.TextChoices):
        PRACTICE_DAYS = 'practice_days', 'Practice days'
        MATCHES_COMPLETED = 'matches_completed', 'Matches completed'
        CHECKLIST_COMPLETIONS = 'checklist_completions', 'Checklist completions'
        MANUAL = 'manual', 'Manual progress'

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        COMPLETED = 'completed', 'Completed'
        ARCHIVED = 'archived', 'Archived'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sports_goals')
    title = models.CharField(max_length=120)
    target = models.PositiveIntegerField()
    unit = models.CharField(max_length=32)
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    progress_source = models.CharField(max_length=24, choices=Source.choices)
    manual_progress = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    reminder_enabled = models.BooleanField(default=False)
    reminder_time = models.TimeField(null=True, blank=True)
    reminder_timezone = models.CharField(max_length=64, default='Asia/Kolkata')
    reminder_last_sent_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(target__gt=0), name='sports_goal_target_positive'),
            models.CheckConstraint(check=models.Q(end_date__gte=models.F('start_date')), name='sports_goal_dates_valid'),
            models.CheckConstraint(check=models.Q(manual_progress__lte=models.F('target')), name='sports_goal_manual_progress_lte_target'),
        ]
        indexes = [models.Index(fields=['user', 'status', 'end_date'], name='sports_goal_user_state_idx')]


class HealthActivityRecord(TimestampedPublicModel):
    class Metric(models.TextChoices):
        STEPS = 'steps', 'Steps'
        DURATION = 'activity_duration', 'Activity duration'
        CALORIES = 'active_calories', 'Active calories'

    class Source(models.TextChoices):
        MANUAL = 'manual', 'Manual entry'
        APPLE_HEALTH = 'apple_health', 'Apple Health'
        HEALTH_CONNECT = 'health_connect', 'Health Connect'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='health_activity_records')
    metric = models.CharField(max_length=24, choices=Metric.choices, db_index=True)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=16)
    recorded_date = models.DateField(db_index=True)
    recorded_timezone = models.CharField(max_length=64, default='Asia/Kolkata')
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL, db_index=True)
    synced_at = models.DateTimeField(default=timezone.now)
    external_record_id = models.CharField(max_length=255, blank=True)
    is_estimated = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(value__gt=0), name='health_activity_value_positive'),
            models.UniqueConstraint(fields=['user', 'source', 'external_record_id'], condition=~models.Q(external_record_id=''), name='unique_health_external_record'),
        ]
        indexes = [models.Index(fields=['user', 'recorded_date', 'metric'], name='health_user_date_metric_idx')]







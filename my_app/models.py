import math
import uuid
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
    full_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(unique=True)
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

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')

    # SportMate player preferences. Legacy statistics remain read-only for now.
    bio = models.TextField(blank=True)
    availability = models.JSONField(default=dict, blank=True)
    preferred_distance = models.PositiveIntegerField(default=15)
    preferred_play_times = models.JSONField(default=list, blank=True)
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
    total_players = models.IntegerField()

    joined_players = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='joined_matches',
        blank=True
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')

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
        ]


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

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='game_players')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='game_participations')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.CONFIRMED, db_index=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

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


class Tournament(TimestampedPublicModel):
    class Format(models.TextChoices): SINGLE_ELIMINATION='single_elimination','Single elimination'; DOUBLE_ELIMINATION='double_elimination','Double elimination'; ROUND_ROBIN='round_robin','Round robin'; LEAGUE='league','League'
    class Status(models.TextChoices): DRAFT='draft','Draft'; OPEN='open','Open'; FULL='full','Full'; STARTED='started','Started'; COMPLETED='completed','Completed'; CANCELLED='cancelled','Cancelled'
    tournament_reference=models.CharField(max_length=20,unique=True,db_index=True); name=models.CharField(max_length=200)
    organizer=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='organized_tournaments'); sport=models.ForeignKey(Sport,on_delete=models.PROTECT,related_name='tournaments')
    community=models.ForeignKey(Community,null=True,blank=True,on_delete=models.SET_NULL,related_name='tournaments'); venue=models.ForeignKey(Venue,on_delete=models.PROTECT,related_name='tournaments'); event=models.OneToOneField(Event,null=True,blank=True,on_delete=models.SET_NULL,related_name='tournament')
    start_date=models.DateField(); end_date=models.DateField(); registration_deadline=models.DateTimeField(); maximum_teams=models.PositiveIntegerField(); format=models.CharField(max_length=24,choices=Format.choices,default=Format.SINGLE_ELIMINATION); skill_level=models.CharField(max_length=16,choices=UserSport.SkillLevel.choices,blank=True); status=models.CharField(max_length=12,choices=Status.choices,default=Status.OPEN,db_index=True)
    class Meta:
        constraints=[models.CheckConstraint(check=models.Q(end_date__gte=models.F('start_date')),name='tournament_date_range_valid'),models.CheckConstraint(check=models.Q(maximum_teams__gt=1),name='tournament_max_teams_valid')]
        indexes=[models.Index(fields=['sport','start_date','status'],name='tournament_sport_date_idx')]


class TournamentTeam(TimestampedPublicModel):
    class Status(models.TextChoices): ACTIVE='active','Active'; ELIMINATED='eliminated','Eliminated'; WITHDRAWN='withdrawn','Withdrawn'; CHAMPION='champion','Champion'
    tournament=models.ForeignKey(Tournament,on_delete=models.CASCADE,related_name='teams'); name=models.CharField(max_length=120); captain=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='captained_tournament_teams'); created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='created_tournament_teams'); seed=models.PositiveIntegerField(null=True,blank=True); status=models.CharField(max_length=12,choices=Status.choices,default=Status.ACTIVE)
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
    rating=models.PositiveSmallIntegerField(); review=models.TextField(blank=True); deleted_at=models.DateTimeField(null=True,blank=True)
    class Meta:
        constraints=[models.CheckConstraint(check=models.Q(rating__gte=1,rating__lte=5),name='player_rating_range_valid'),models.UniqueConstraint(fields=['reviewer','player','game'],name='unique_player_game_rating')]
        indexes=[models.Index(fields=['player','created_at'],name='player_rating_created_idx')]


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

class RecommendationProfile(TimestampedPublicModel):
    user=models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='recommendation_profile')
    preferences=models.JSONField(default=dict,blank=True)
    last_evaluated_at=models.DateTimeField(null=True,blank=True)


# ---------------------------------------------------------------------------
# PLATFORM MODERATION
# ---------------------------------------------------------------------------
class Report(TimestampedPublicModel):
    class TargetType(models.TextChoices):
        USER = 'user', 'User'
        PLAYER_PROFILE = 'player_profile', 'Player profile'
        GAME = 'game', 'Game'
        COMMUNITY = 'community', 'Community'
        COMMUNITY_POST = 'community_post', 'Community post'
        COMMUNITY_COMMENT = 'community_comment', 'Community comment'
        MESSAGE = 'message', 'Message'
        EVENT = 'event', 'Event'
        TOURNAMENT = 'tournament', 'Tournament'
        RATING = 'rating', 'Rating'
        VENUE_REVIEW = 'venue_review', 'Venue review'

    class Reason(models.TextChoices):
        SPAM = 'spam', 'Spam'
        HARASSMENT = 'harassment', 'Harassment'
        ABUSE = 'abuse', 'Abuse'
        INAPPROPRIATE_CONTENT = 'inappropriate_content', 'Inappropriate content'
        FAKE_PROFILE = 'fake_profile', 'Fake profile'
        FRAUD = 'fraud', 'Fraud'
        CHEATING = 'cheating', 'Cheating'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        UNDER_REVIEW = 'under_review', 'Under review'
        RESOLVED = 'resolved', 'Resolved'
        DISMISSED = 'dismissed', 'Dismissed'

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='reports_created')
    target_type = models.CharField(max_length=24, choices=TargetType.choices, db_index=True)
    target_public_id = models.UUIDField(db_index=True)
    reason = models.CharField(max_length=24, choices=Reason.choices, db_index=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    assigned_moderator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='reports_assigned')
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'created_at'], name='report_status_created_idx'),
            models.Index(fields=['target_type', 'target_public_id'], name='report_target_idx'),
            models.Index(fields=['reporter', 'created_at'], name='report_reporter_created_idx'),
        ]


class ModerationAction(TimestampedPublicModel):
    class Action(models.TextChoices):
        WARN = 'warn', 'Warn'
        HIDE = 'hide', 'Hide'
        REMOVE = 'remove', 'Remove'
        SUSPEND = 'suspend', 'Suspend'
        BAN = 'ban', 'Ban'
        RESTORE = 'restore', 'Restore'

    moderator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='moderation_actions')
    report = models.ForeignKey(Report, null=True, blank=True, on_delete=models.SET_NULL, related_name='actions')
    target_type = models.CharField(max_length=24, choices=Report.TargetType.choices, db_index=True)
    target_public_id = models.UUIDField(db_index=True)
    action = models.CharField(max_length=12, choices=Action.choices, db_index=True)
    reason = models.TextField(blank=True)
    previous_state = models.JSONField(default=dict, blank=True)
    new_state = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=['target_type', 'target_public_id', 'created_at'], name='moderation_target_time_idx')]


class UserModeration(TimestampedPublicModel):
    """Current and historical platform restrictions; never client writable."""
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ENDED = 'ended', 'Ended'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='moderation_records')
    moderator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='user_moderations_issued')
    action = models.CharField(max_length=12, choices=ModerationAction.Action.choices)
    reason = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'status'], name='user_moderation_status_idx')]

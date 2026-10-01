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
class Ground(TimestampedPublicModel):
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)

    latitude = models.FloatField()
    longitude = models.FloatField()

    price_per_hour = models.FloatField(default=0)
    pitch_type = models.CharField(max_length=50, blank=True)
    sports = models.ManyToManyField(Sport, related_name='grounds', blank=True)

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
# MESSAGE (CHAT)
# ---------------------------------------------------------------------------
class Message(TimestampedPublicModel):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_messages')

    content = models.TextField()

    is_read = models.BooleanField(default=False)
    is_delivered = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=['receiver', 'is_read', 'created_at'], name='message_receiver_read_idx')]
        constraints = [
            models.CheckConstraint(check=~models.Q(sender=models.F('receiver')), name='message_sender_differs_from_receiver'),
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
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')

    type = models.CharField(max_length=20, choices=TYPES)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)

    is_read = models.BooleanField(default=False)
    priority = models.IntegerField(default=1)

    data = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=['user', 'is_read', 'created_at'], name='notification_user_read_idx')]

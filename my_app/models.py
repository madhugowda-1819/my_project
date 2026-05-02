import math
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils import timezone


# ---------------------------------------------------------------------------
# USER
# ---------------------------------------------------------------------------
class User(AbstractUser):
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

    # 🔥 NEW (for live status)
    last_seen = models.DateTimeField(auto_now=True)

    bio = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
class Sport(models.Model):
    name = models.CharField(max_length=50, unique=True)
    icon = models.CharField(max_length=50, blank=True)
    category = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# PLAYER PROFILE
# ---------------------------------------------------------------------------
class PlayerProfile(models.Model):
    SKILL_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
        ('pro', 'Pro'),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')

    skill_level = models.CharField(max_length=20, choices=SKILL_CHOICES, default='beginner')
    matches_played = models.PositiveIntegerField(default=0)
    matches_won = models.PositiveIntegerField(default=0)

    rating = models.FloatField(default=0.0)
    achievements = models.JSONField(default=list, blank=True)

    def win_rate(self):
        if self.matches_played == 0:
            return 0
        return (self.matches_won / self.matches_played) * 100


# ---------------------------------------------------------------------------
# 🔥 NEW: GROUNDS (IMPORTANT FOR YOUR UI)
# ---------------------------------------------------------------------------
class Ground(models.Model):
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=100)

    latitude = models.FloatField()
    longitude = models.FloatField()

    price_per_hour = models.FloatField(default=0)
    pitch_type = models.CharField(max_length=50, blank=True)

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


# ---------------------------------------------------------------------------
# AVAILABILITY
# ---------------------------------------------------------------------------
class AvailabilitySlot(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='availability_slots')

    day_of_week = models.IntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    label = models.CharField(max_length=50, blank=True)


# ---------------------------------------------------------------------------
# MATCH (UPDATED 🔥)
# ---------------------------------------------------------------------------
class Match(models.Model):
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


# ---------------------------------------------------------------------------
# MESSAGE (CHAT)
# ---------------------------------------------------------------------------
class Message(models.Model):
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='received_messages')

    content = models.TextField()

    is_read = models.BooleanField(default=False)
    is_delivered = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)


# ---------------------------------------------------------------------------
# NOTIFICATION (UPDATED 🔥)
# ---------------------------------------------------------------------------
class Notification(models.Model):
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

    created_at = models.DateTimeField(auto_now_add=True)
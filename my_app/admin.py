from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    User, Sport, PlayerProfile, AvailabilitySlot,
    Match, Message, Notification, Ground
)


# ------------------------------------------------------------------
# USER ADMIN
# ------------------------------------------------------------------
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'username', 'phone', 'city', 'is_available', 'is_online', 'latitude', 'longitude')
    search_fields = ('email', 'username', 'phone')
    ordering = ('email',)
    
    # Add location fields to the edit form
    fieldsets = UserAdmin.fieldsets + (
        ('Location', {
            'fields': ('latitude', 'longitude', 'city')
        }),
        ('Sports & Availability', {
            'fields': ('sports', 'is_available', 'is_online')
        }),
        ('Profile', {
            'fields': ('full_name', 'phone', 'avatar', 'bio')
        }),
    )

# ------------------------------------------------------------------
# SPORT
# ------------------------------------------------------------------
@admin.register(Sport)
class SportAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'is_active')
    search_fields = ('name',)


# ------------------------------------------------------------------
# PLAYER PROFILE
# ------------------------------------------------------------------
@admin.register(PlayerProfile)
class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'skill_level', 'rating', 'matches_played', 'matches_won')


# ------------------------------------------------------------------
# AVAILABILITY
# ------------------------------------------------------------------
@admin.register(AvailabilitySlot)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ('user', 'day_of_week', 'start_time', 'end_time', 'label')


# ------------------------------------------------------------------
# 🔥 GROUND (NEW)
# ------------------------------------------------------------------
@admin.register(Ground)
class GroundAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'price_per_hour', 'pitch_type')
    search_fields = ('name', 'city')


# ------------------------------------------------------------------
# 🔥 MATCH (UPDATED)
# ------------------------------------------------------------------
@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ('sport', 'organizer', 'ground', 'date_time', 'total_players', 'status')
    list_filter = ('status', 'sport')


# ------------------------------------------------------------------
# MESSAGE
# ------------------------------------------------------------------
@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'receiver', 'content', 'created_at')
    search_fields = ('sender__username', 'receiver__username')


# ------------------------------------------------------------------
# NOTIFICATION
# ------------------------------------------------------------------
@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'type', 'title', 'is_read', 'created_at')
    list_filter = ('type', 'is_read')
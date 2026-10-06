from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    User, Sport, PlayerProfile, AvailabilitySlot,
    Match, Message, Notification, Ground, Venue, Court, VenueSport, Report, ModerationAction, UserModeration
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
    list_display = ('name', 'city', 'size', 'price_per_hour', 'pitch_type', 'source')
    list_filter = ('size', 'sports', 'source')
    search_fields = ('name', 'city', 'address', 'maps_place_id')
    filter_horizontal = ('sports',)


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ('name', 'city', 'opening_time', 'closing_time', 'active', 'source_ground')
    list_filter = ('active', 'city')
    search_fields = ('name', 'city', 'address', 'source_ground__name')
    autocomplete_fields = ('source_ground',)


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    list_display = ('name', 'venue', 'sport', 'capacity', 'price_per_hour', 'active')
    list_filter = ('active', 'sport')
    search_fields = ('name', 'venue__name')


@admin.register(VenueSport)
class VenueSportAdmin(admin.ModelAdmin):
    list_display = ('venue', 'sport')


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


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('public_id', 'reporter', 'target_type', 'reason', 'status', 'assigned_moderator', 'created_at')
    list_filter = ('status', 'reason', 'target_type')
    search_fields = ('reporter__email', 'description', 'resolution')
    readonly_fields = ('public_id', 'created_at', 'updated_at', 'resolved_at')


@admin.register(ModerationAction)
class ModerationActionAdmin(admin.ModelAdmin):
    list_display = ('moderator', 'target_type', 'action', 'created_at')
    list_filter = ('action', 'target_type')
    readonly_fields = ('public_id', 'moderator', 'report', 'target_type', 'target_public_id', 'action', 'reason', 'previous_state', 'new_state', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(UserModeration)
class UserModerationAdmin(admin.ModelAdmin):
    list_display = ('user', 'moderator', 'action', 'status', 'starts_at', 'ends_at')
    list_filter = ('action', 'status')
    readonly_fields = ('public_id', 'created_at', 'updated_at')

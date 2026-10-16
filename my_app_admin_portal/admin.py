from django.contrib import admin

from my_app.models import Achievement, SportChallenge
from .models import ModerationAction, Report, RoleAuditLog, UserModeration


@admin.register(RoleAuditLog)
class RoleAuditLogAdmin(admin.ModelAdmin):
    list_display = ('actor', 'target_user', 'role', 'action', 'created_at')
    list_filter = ('role', 'action')
    readonly_fields = ('public_id', 'created_at', 'updated_at')


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


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    """Platform staff manage definitions; player progress remains server-owned."""
    list_display = ('name', 'code', 'category', 'target_value', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('name', 'code', 'description')
    readonly_fields = ('public_id', 'created_at', 'updated_at')


@admin.register(SportChallenge)
class SportChallengeAdmin(admin.ModelAdmin):
    """Only Django staff can publish challenge rules and reward definitions."""
    list_display = ('name', 'sport', 'kind', 'metric', 'target', 'starts_on', 'ends_on', 'is_active')
    list_filter = ('kind', 'metric', 'is_active', 'starts_on', 'ends_on')
    search_fields = ('name', 'description')
    readonly_fields = ('public_id', 'created_at', 'updated_at')

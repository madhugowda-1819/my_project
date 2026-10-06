from rest_framework.permissions import BasePermission
from django.utils import timezone


MODERATOR_GROUPS = {'SportMate Moderators', 'SportMate Admins', 'SportMate Support'}
ADMIN_GROUPS = {'SportMate Admins'}


def has_platform_role(user, groups):
    """Use Django Groups/permissions instead of a parallel role field."""
    return bool(
        user and user.is_authenticated and (
            user.is_superuser or user.has_perm('my_app.change_report')
            or user.groups.filter(name__in=groups).exists()
        )
    )


class IsActiveAccount(BasePermission):
    """Allows only authenticated accounts that are not suspended or deactivated."""

    message = 'This account is suspended or deactivated.'

    def has_permission(self, request, view):
        user = request.user
        # Temporary suspensions become inactive records when their server-set
        # end date passes; bans have no end date and require an explicit restore.
        if user and user.is_authenticated and user.account_status == user.AccountStatus.SUSPENDED:
            expired = user.moderation_records.filter(
                action='suspend', status='active', ends_at__isnull=False, ends_at__lte=timezone.now(),
            )
            if expired.exists():
                expired.update(status='ended', updated_at=timezone.now())
                user.account_status = user.AccountStatus.ACTIVE
                user.save(update_fields=['account_status', 'updated_at'])
        return bool(
            user and user.is_authenticated and user.is_active
            and user.account_status == user.AccountStatus.ACTIVE
        )


class IsPlatformModerator(BasePermission):
    message = 'Platform moderator permission is required.'

    def has_permission(self, request, view):
        return has_platform_role(request.user, MODERATOR_GROUPS)


class IsPlatformAdmin(BasePermission):
    message = 'Platform administrator permission is required.'

    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and (
                request.user.is_superuser or request.user.has_perm('my_app.view_user')
                or request.user.groups.filter(name__in=ADMIN_GROUPS).exists()
            )
        )

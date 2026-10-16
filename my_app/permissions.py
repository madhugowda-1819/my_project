from rest_framework.permissions import BasePermission
from django.utils import timezone


MODERATOR_GROUPS = {'SportMate Moderators', 'SportMate Admins', 'SportMate Support'}
ADMIN_GROUPS = {'SportMate Admins'}
VENUE_OWNER_GROUP = 'SportMate Venue Owners'
COMMUNITY_MODERATOR_GROUP = 'SportMate Community Moderators'


def is_venue_owner(user):
    return bool(user and user.is_authenticated and (
        not user.is_superuser and user.groups.filter(name=VENUE_OWNER_GROUP).exists()
    ))


def has_platform_role(user, groups):
    """Check explicit platform roles, not incidental model permissions."""
    return bool(
        user and user.is_authenticated and (
            user.is_superuser or user.groups.filter(name__in=groups).exists()
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


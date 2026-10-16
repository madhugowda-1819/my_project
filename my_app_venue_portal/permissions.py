from rest_framework.permissions import BasePermission

from my_app.permissions import ADMIN_GROUPS, VENUE_OWNER_GROUP


class IsApprovedVenueOwner(BasePermission):
    """Allows active accounts explicitly approved for venue operations."""

    message = 'Approved venue-owner access is required.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active
            and user.account_status == user.AccountStatus.ACTIVE
            and user.groups.filter(name=VENUE_OWNER_GROUP).exists()
            and not user.is_superuser
            and not user.groups.filter(name__in=ADMIN_GROUPS).exists()
        )

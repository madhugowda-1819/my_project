from rest_framework.permissions import BasePermission


class IsActiveAccount(BasePermission):
    """Allows only authenticated accounts that are not suspended or deactivated."""

    message = 'This account is suspended or deactivated.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active
            and user.account_status == user.AccountStatus.ACTIVE
        )

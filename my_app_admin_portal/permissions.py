from rest_framework.permissions import BasePermission

from my_app.permissions import ADMIN_GROUPS, MODERATOR_GROUPS, has_platform_role


class IsPlatformModerator(BasePermission):
    message = 'Platform moderator permission is required.'

    def has_permission(self, request, view):
        return has_platform_role(request.user, MODERATOR_GROUPS)


class IsPlatformAdmin(BasePermission):
    message = 'Platform administrator permission is required.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated
            and (user.is_superuser or user.groups.filter(name__in=ADMIN_GROUPS).exists())
        )

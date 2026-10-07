"""Small utilities used by the API (and legacy notification helper)."""
from ..services.notifications import NotificationService


def create_notification(user, type, title, body, data=None, priority=1):
    """Create a notification; retained for existing service imports."""
    return NotificationService.create_notification(
        user=user,
        type=type,
        title=title,
        body=body,
        data=data or {},
        priority=priority,
    )

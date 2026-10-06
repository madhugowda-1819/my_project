from .services.notifications import NotificationService


def create_notification(user, type, title, body, data=None, priority=1):
    """
    Generic notification creator
    """
    if data is None:
        data = {}

    return NotificationService.create_notification(user=user, type=type, title=title, body=body, data=data, priority=priority)

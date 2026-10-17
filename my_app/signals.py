import os

from django.contrib.auth import get_user_model
from django.db.models.signals import post_migrate
from django.dispatch import receiver


def _enabled(value):
    return str(value).lower() in ('1', 'true', 'yes', 'on')


@receiver(post_migrate)
def create_configured_superuser(sender, **kwargs):
    """Create the configured owner account after migrations, once only."""
    if sender.name != 'my_app' or not _enabled(os.getenv('DJANGO_SUPERUSER_AUTO_CREATE')):
        return

    email = os.getenv('DJANGO_SUPERUSER_EMAIL')
    username = os.getenv('DJANGO_SUPERUSER_USERNAME')
    password = os.getenv('DJANGO_SUPERUSER_PASSWORD')
    if not all((email, username, password)):
        return

    User = get_user_model()
    if User.objects.filter(email__iexact=email).exists():
        return

    User.objects.create_superuser(email=email, username=username, password=password)

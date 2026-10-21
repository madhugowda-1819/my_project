from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
import logging

from my_app.models import SportsGround


class Command(BaseCommand):
    help = 'Remove expired external discovery grounds that are not referenced.'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=24)
        deleted, _ = SportsGround.objects.filter(
            expires_at__lt=timezone.now(), updated_at__lt=cutoff,
            tournament_venue__isnull=True,
        ).delete()
        message = f'Removed {deleted} expired discovery records.'
        logging.getLogger('my_app.ground_cache').info(message)
        self.stdout.write(self.style.SUCCESS(message))

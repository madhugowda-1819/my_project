from django.core.management.base import BaseCommand
from django.db import transaction

from my_app.models import PlayerProfile, PublicIdCounter, User
from my_app.services.public_ids import assign_user_public_id


class Command(BaseCommand):
    help = 'Assign missing permanent SportMate player and venue-owner IDs.'

    def handle(self, *args, **options):
        player_count = 0
        owner_count = 0
        with transaction.atomic():
            players = User.objects.filter(player_id__isnull=True, profile__isnull=False).order_by('created_at', 'pk')
            for user in players:
                assign_user_public_id(user, kind=PublicIdCounter.Kind.PLAYER)
                player_count += 1
            owners = User.objects.filter(venue_owner_id__isnull=True, venue_owner_applications__isnull=False).distinct().order_by('created_at', 'pk')
            for user in owners:
                assign_user_public_id(user, kind=PublicIdCounter.Kind.VENUE_OWNER)
                owner_count += 1
        self.stdout.write(self.style.SUCCESS(f'Assigned {player_count} player IDs and {owner_count} venue-owner IDs.'))

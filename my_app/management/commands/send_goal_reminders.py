from django.core.management.base import BaseCommand
from my_app.services.goals import SportsGoalService


class Command(BaseCommand):
    help = 'Deliver due opt-in sports goal reminders through SportMate notifications.'

    def handle(self, *args, **options):
        sent = SportsGoalService.deliver_due_reminders()
        self.stdout.write(self.style.SUCCESS(f'Delivered {sent} goal reminder(s).'))

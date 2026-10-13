from django.core.management.base import BaseCommand
from my_app.services.events import EventReminderService


class Command(BaseCommand):
    help = 'Deliver due opt-in event reminders through SportMate notifications.'

    def handle(self, *args, **options):
        sent = EventReminderService.deliver_due()
        self.stdout.write(self.style.SUCCESS(f'Delivered {sent} event reminder(s).'))

"""Create local-only, bookable Cricket and Badminton inventory for testing."""
from django.core.management.base import BaseCommand, CommandError
from my_app.models import Court, Sport, Venue, VenueAmenity, VenueSport


class Command(BaseCommand):
    help = 'Create/update a demo venue with Cricket and Badminton courts near supplied coordinates.'

    def add_arguments(self, parser):
        parser.add_argument('--latitude', type=float, required=True)
        parser.add_argument('--longitude', type=float, required=True)
        parser.add_argument('--city', default='Demo City')

    def handle(self, *args, **options):
        latitude, longitude = options['latitude'], options['longitude']
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise CommandError('Provide valid latitude and longitude values.')
        cricket, _ = Sport.objects.get_or_create(name='Cricket', defaults={'slug': 'cricket', 'is_active': True})
        badminton, _ = Sport.objects.get_or_create(name='Badminton', defaults={'slug': 'badminton', 'is_active': True})
        venue, created = Venue.objects.update_or_create(
            name='SportMate Demo Arena',
            defaults={
                'description': 'Development-only demo venue for booking and AI matching tests.',
                'address': 'Demo Sports Road', 'city': options['city'],
                'latitude': latitude, 'longitude': longitude,
                'opening_time': '06:00', 'closing_time': '23:00', 'active': True,
            },
        )
        for sport, name, capacity, price in ((cricket, 'Cricket Turf 1', 12, 1200), (badminton, 'Badminton Court 1', 4, 500)):
            VenueSport.objects.get_or_create(venue=venue, sport=sport)
            Court.objects.update_or_create(venue=venue, name=name, defaults={'sport': sport, 'capacity': capacity, 'price_per_hour': price, 'active': True})
        VenueAmenity.objects.get_or_create(venue=venue, defaults={'parking': True, 'washroom': True, 'drinking_water': True, 'lighting': True})
        action = 'Created' if created else 'Updated'
        self.stdout.write(self.style.SUCCESS(f'{action} {venue.name} at {latitude}, {longitude} with Cricket and Badminton courts.'))

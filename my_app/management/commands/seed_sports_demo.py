from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from my_app.models import Ground, PlayerProfile, Sport


class Command(BaseCommand):
    help = 'Creates demo players and small, medium, and big grounds for Cricket, Volleyball, and Badminton.'

    SPORTS = ('Cricket', 'Volleyball', 'Badminton')
    # Bengaluru coordinates keep the data discoverable when testing locally.
    GROUND_DATA = {
        'Cricket': [
            ('Willow Box Cricket', 'small', 12.9721, 77.5946, 700),
            ('Central Cricket Ground', 'medium', 12.9780, 77.6012, 1200),
            ('Lakeside Cricket Stadium', 'big', 12.9658, 77.5855, 2500),
        ],
        'Volleyball': [
            ('Spike Mini Court', 'small', 12.9710, 77.6000, 400),
            ('City Volleyball Court', 'medium', 12.9764, 77.5900, 700),
            ('Arena Volleyball Complex', 'big', 12.9635, 77.5970, 1300),
        ],
        'Badminton': [
            ('Shuttle Mini Court', 'small', 12.9740, 77.5860, 350),
            ('Metro Badminton Hall', 'medium', 12.9685, 77.6060, 600),
            ('Champion Badminton Arena', 'big', 12.9610, 77.5905, 1000),
        ],
    }

    def handle(self, *args, **options):
        user_model = get_user_model()
        sports = {}
        for name in self.SPORTS:
            sport, _ = Sport.objects.get_or_create(name=name, defaults={'category': 'Main sport'})
            sport.is_active = True
            sport.save(update_fields=['is_active'])
            sports[name] = sport

            for number in (1, 2):
                username = f'{name.lower()}_player_{number}'
                email = f'{username}@sportmate.demo'
                user, created = user_model.objects.get_or_create(
                    username=username,
                    defaults={
                        'email': email,
                        'full_name': f'{name} Player {number}',
                        'city': 'Bengaluru',
                        'latitude': 12.9716 + (number * .001),
                        'longitude': 77.5946 + (number * .001),
                        'is_available': True,
                    },
                )
                if created:
                    user.set_password('SportMateDemo123!')
                    user.save()
                user.sports.add(sport)
                PlayerProfile.objects.get_or_create(user=user, defaults={'skill_level': 'intermediate', 'rating': 3.5})

        for sport_name, grounds in self.GROUND_DATA.items():
            sport = sports[sport_name]
            for name, size, latitude, longitude, price in grounds:
                ground, _ = Ground.objects.update_or_create(
                    name=name,
                    defaults={
                        'city': 'Bengaluru',
                        'latitude': latitude,
                        'longitude': longitude,
                        'price_per_hour': price,
                        'pitch_type': 'Indoor' if sport_name == 'Badminton' else 'Outdoor',
                        'size': size,
                        'address': f'{size.title()} {sport_name} facility, Bengaluru',
                        'source': 'demo',
                    },
                )
                ground.sports.set([sport])

        self.stdout.write(self.style.SUCCESS('Seeded 6 demo players and 9 sport grounds.'))

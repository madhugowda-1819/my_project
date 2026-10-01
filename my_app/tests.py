from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
from rest_framework.test import APITestCase

from .models import Ground, Match, PlayerProfile, Sport, UserSport, UserBlock, Venue, VenueSport, Court, VenueAmenity, CourtBooking, VenueReview, CourtBlockedPeriod
from .services.locations import nearby_queryset
from .services.player_matching import PlayerMatchingService
from .services.availability import VenueAvailabilityService
from .services.bookings import BookingStatusService

User = get_user_model()


class BasicTests(TestCase):
    def setUp(self):
        Sport.objects.filter(name='Cricket').delete()

    def test_create_user(self):
        u = User.objects.create_user(username='john', email='j@x.com',
                                     password='pw123456')
        self.assertEqual(u.email, 'j@x.com')

    def test_create_sport(self):
        s = Sport.objects.create(name='Cricket', icon='🏏')
        self.assertEqual(str(s), 'Cricket')


class MatchApiTests(APITestCase):
    def setUp(self):
        self.sport = Sport.objects.get(name='Football')
        self.ground = Ground.objects.create(
            name='Central Ground', city='Bengaluru', latitude=12.9716,
            longitude=77.5946, price_per_hour=500,
        )
        self.organizer = User.objects.create_user(username='organizer', email='organizer@example.com', password='pw123456')
        self.player = User.objects.create_user(username='player', email='player@example.com', password='pw123456')
        PlayerProfile.objects.create(user=self.organizer)
        PlayerProfile.objects.create(user=self.player)
        self.organizer.latitude = self.player.latitude = 12.9716
        self.organizer.longitude = self.player.longitude = 77.5946
        self.organizer.save()
        self.player.save()

    def test_v1_create_and_join_match_is_idempotent(self):
        self.client.force_authenticate(self.organizer)
        future = timezone.now() + timedelta(days=1)
        create = self.client.post('/api/v1/matches/create/', {
            'sport': self.sport.id,
            'ground': self.ground.id,
            'date_time': future.isoformat(),
            'total_players': 2,
        }, format='json')
        self.assertEqual(create.status_code, 201)
        match_id = create.data['match']['id']

        self.client.force_authenticate(self.player)
        first_join = self.client.post(f'/api/v1/matches/{match_id}/join/')
        second_join = self.client.post(f'/api/v1/matches/{match_id}/join/')

        self.assertEqual(first_join.status_code, 200)
        self.assertEqual(second_join.status_code, 200)
        self.assertEqual(Match.objects.get(pk=match_id).joined_count(), 1)
        self.assertEqual(self.organizer.notifications.count(), 1)

    def test_validation_errors_use_standard_envelope(self):
        self.client.force_authenticate(self.organizer)
        response = self.client.post('/api/v1/matches/create/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['error']['code'], 'VALIDATION_ERROR')

    def test_ai_recommendations_return_paginated_matches(self):
        Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(days=1), total_players=2,
        )
        self.client.force_authenticate(self.player)
        response = self.client.get('/api/v1/ai/matches/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['count'], 1)


class AuthenticationApiTests(APITestCase):
    register_url = '/api/v1/auth/register/'
    login_url = '/api/v1/auth/login/'

    def setUp(self):
        cache.clear()

    def payload(self, **overrides):
        data = {
            'name': 'Asha Player', 'username': 'asha', 'email': 'asha@example.com',
            'phone': '+919876543210', 'password': 'StrongPass!123',
        }
        data.update(overrides)
        return data

    def create_user(self, **overrides):
        user = User.objects.create_user(
            username=overrides.get('username', 'existing'),
            email=overrides.get('email', 'existing@example.com'),
            password=overrides.get('password', 'StrongPass!123'),
        )
        PlayerProfile.objects.create(user=user)
        return user

    def test_successful_registration_hashes_password_and_returns_tokens(self):
        response = self.client.post(self.register_url, self.payload(), format='json')
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='asha@example.com')
        self.assertTrue(user.check_password('StrongPass!123'))
        self.assertNotEqual(user.password, 'StrongPass!123')
        self.assertTrue(PlayerProfile.objects.filter(user=user).exists())
        self.assertIn('access', response.data['tokens'])
        self.assertNotIn('password', response.data['user'])

    def test_duplicate_email_and_username_are_rejected(self):
        self.create_user()
        email_response = self.client.post(self.register_url, self.payload(email='existing@example.com'), format='json')
        username_response = self.client.post(self.register_url, self.payload(username='existing'), format='json')
        self.assertEqual(email_response.status_code, 400)
        self.assertIn('email', email_response.data['error']['details'])
        self.assertEqual(username_response.status_code, 400)
        self.assertIn('username', username_response.data['error']['details'])

    def test_invalid_password_is_rejected(self):
        response = self.client.post(self.register_url, self.payload(password='12345678'), format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('password', response.data['error']['details'])

    def test_login_invalid_login_and_suspended_account(self):
        user = self.create_user()
        success = self.client.post(self.login_url, {'identifier': user.email.upper(), 'password': 'StrongPass!123'}, format='json')
        invalid = self.client.post(self.login_url, {'identifier': user.email, 'password': 'wrong'}, format='json')
        user.account_status = User.AccountStatus.SUSPENDED
        user.save()
        suspended = self.client.post(self.login_url, {'identifier': user.email, 'password': 'StrongPass!123'}, format='json')
        self.assertEqual(success.status_code, 200)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(suspended.status_code, 403)

    def test_suspended_account_cannot_use_protected_endpoints(self):
        user = self.create_user()
        login = self.client.post(self.login_url, {'identifier': user.email, 'password': 'StrongPass!123'}, format='json')
        user.account_status = User.AccountStatus.SUSPENDED
        user.save()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['tokens']['access']}")
        response = self.client.get('/api/v1/users/me/')
        self.assertEqual(response.status_code, 403)

    def test_token_refresh_logout_and_profile_flow(self):
        user = self.create_user()
        login = self.client.post(self.login_url, {'identifier': user.username, 'password': 'StrongPass!123'}, format='json')
        refresh = login.data['tokens']['refresh']
        token_response = self.client.post('/api/v1/auth/token/refresh/', {'refresh': refresh}, format='json')
        self.assertEqual(token_response.status_code, 200)
        self.assertIn('access', token_response.data)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['tokens']['access']}")
        profile = self.client.get('/api/v1/users/me/')
        update = self.client.patch('/api/v1/users/me/', {'name': 'Asha Updated', 'city': 'Bengaluru', 'bio': 'Runner'}, format='json')
        logout = self.client.post('/api/v1/auth/logout/', {'refresh': refresh}, format='json')
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data['name'], 'Asha Updated')
        self.assertEqual(logout.status_code, 200)


class PlayerProfileApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', email='owner@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.user)
        self.other = User.objects.create_user(
            username='public-player', email='private@example.com', phone='+919876543210',
            password='StrongPass!123', full_name='Public Player', city='Bengaluru', latitude=12.9716, longitude=77.5946,
        )
        PlayerProfile.objects.create(user=self.other, bio='Public bio', profile_visibility='public')
        self.sport = Sport.objects.get(name='Badminton')
        self.client.force_authenticate(self.user)

    def test_create_player_sport_and_duplicate_sport_validation(self):
        payload = {'sports': [{'sport_id': self.sport.id, 'skill_level': 'advanced', 'rating': '4.5', 'preferred': True}]}
        response = self.client.patch('/api/v1/users/me/sports/', payload, format='json')
        duplicate = self.client.patch('/api/v1/users/me/sports/', {'sports': payload['sports'] * 2}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserSport.objects.get(user=self.user, sport=self.sport).rating, 4.5)
        self.assertEqual(duplicate.status_code, 400)

    def test_invalid_skill_and_backend_owned_statistics_are_rejected(self):
        invalid_skill = self.client.patch('/api/v1/users/me/sports/', {
            'sports': [{'sport_id': self.sport.id, 'skill_level': 'legend', 'rating': '3.0'}]
        }, format='json')
        protected_stats = self.client.patch('/api/v1/users/me/sports/', {
            'sports': [{'sport_id': self.sport.id, 'skill_level': 'pro', 'rating': '5.0', 'wins': 99}]
        }, format='json')
        self.assertEqual(invalid_skill.status_code, 400)
        self.assertEqual(protected_stats.status_code, 200)
        self.assertEqual(UserSport.objects.get(user=self.user, sport=self.sport).wins, 0)

    def test_player_profile_update_and_public_privacy(self):
        update = self.client.patch('/api/v1/users/me/player-profile/', {
            'bio': 'Available evenings', 'preferred_distance': 25,
            'preferred_play_times': ['evening'], 'profile_visibility': 'sports_only',
        }, format='json')
        detail = self.client.get(f'/api/v1/players/{self.other.id}/')
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data['player_profile']['preferred_distance'], 25)
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn('email', detail.data)
        self.assertNotIn('phone', detail.data)
        self.assertNotIn('latitude', detail.data)
        self.assertNotIn('longitude', detail.data)
        self.assertEqual(detail.data['location'], 'Bengaluru')

    def test_private_profiles_are_not_public(self):
        self.other.profile.profile_visibility = 'private'
        self.other.profile.save()
        response = self.client.get(f'/api/v1/players/{self.other.id}/')
        self.assertEqual(response.status_code, 404)


class NearbySearchApiTests(APITestCase):
    def setUp(self):
        self.requester = User.objects.create_user(
            username='nearby-owner', email='nearby-owner@example.com', password='StrongPass!123'
        )
        PlayerProfile.objects.create(user=self.requester)
        self.sport = Sport.objects.get(name='Badminton')
        self.near = User.objects.create_user(
            username='near-player', email='near@example.com', password='StrongPass!123',
            full_name='Near Player', city='Bengaluru', latitude=12.9720, longitude=77.5948,
        )
        self.far = User.objects.create_user(
            username='far-player', email='far@example.com', password='StrongPass!123',
            full_name='Far Player', city='Bengaluru', latitude=13.2000, longitude=77.8000,
        )
        PlayerProfile.objects.create(user=self.near, profile_visibility='public')
        PlayerProfile.objects.create(user=self.far, profile_visibility='public')
        UserSport.objects.create(user=self.near, sport=self.sport, skill_level='advanced', rating=4.0)
        UserSport.objects.create(user=self.far, sport=self.sport, skill_level='beginner', rating=2.0)
        self.venue_near = Ground.objects.create(name='Near Venue', city='Bengaluru', latitude=12.9721, longitude=77.5949)
        self.venue_far = Ground.objects.create(name='Far Venue', city='Bengaluru', latitude=13.2, longitude=77.8)
        self.venue_near.sports.add(self.sport)
        self.venue_far.sports.add(self.sport)
        self.client.force_authenticate(self.requester)

    def test_haversine_distance_radius_filter_and_sorting(self):
        grounds = list(nearby_queryset(Ground.objects.all(), latitude=12.9716, longitude=77.5946, radius=2))
        self.assertEqual([ground.name for ground in grounds], ['Near Venue'])
        self.assertLess(grounds[0].distance_km, 1)

    def test_nearby_players_returns_public_safe_sorted_results(self):
        response = self.client.get('/api/v1/players/nearby/', {
            'latitude': 12.9716, 'longitude': 77.5946, 'radius': 2,
            'sport': 'badminton', 'skill_level': 'advanced',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        player = response.data['results'][0]
        self.assertEqual(player['username'], 'near-player')
        self.assertNotIn('email', player)
        self.assertNotIn('latitude', player)
        self.assertTrue(player['availability'])

    def test_nearby_venues_and_invalid_or_missing_coordinates(self):
        venues = self.client.get('/api/v1/grounds/', {
            'latitude': 12.9716, 'longitude': 77.5946, 'radius': 2, 'sport': 'badminton',
        })
        invalid = self.client.get('/api/v1/players/nearby/', {'latitude': 200, 'longitude': 77})
        missing = self.client.get('/api/v1/venues/nearby/', {'latitude': 12.9})
        self.assertEqual(venues.status_code, 200)
        self.assertEqual(venues.data['results'][0]['name'], 'Near Venue')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(missing.status_code, 400)


class PlayerMatchingServiceTests(APITestCase):
    def setUp(self):
        self.requester = User.objects.create_user(
            username='matcher', email='matcher@example.com', password='StrongPass!123',
            latitude=12.9716, longitude=77.5946,
        )
        PlayerProfile.objects.create(user=self.requester, preferred_play_times=['evening', 'weekend'])
        self.sport = Sport.objects.get(name='Badminton')
        UserSport.objects.create(user=self.requester, sport=self.sport, skill_level='advanced', rating=4)
        self.same = self.create_candidate('same', 12.9720, 77.5948, 'advanced', ['evening', 'weekend'])
        self.close_skill = self.create_candidate('close-skill', 12.9800, 77.6000, 'expert', ['evening', 'morning'])
        self.other_sport = self.create_candidate('other-sport', 12.9721, 77.5949, 'advanced', ['evening'])
        tennis = Sport.objects.get(name='Tennis')
        UserSport.objects.filter(user=self.other_sport, sport=self.sport).delete()
        UserSport.objects.create(user=self.other_sport, sport=tennis, skill_level='advanced', rating=4)
        self.client.force_authenticate(self.requester)

    def create_candidate(self, username, latitude, longitude, skill, times):
        candidate = User.objects.create_user(
            username=username, email=f'{username}@example.com', password='StrongPass!123',
            full_name=username, latitude=latitude, longitude=longitude,
        )
        PlayerProfile.objects.create(user=candidate, profile_visibility='public', preferred_play_times=times)
        UserSport.objects.create(user=candidate, sport=self.sport, skill_level=skill, rating=4)
        return candidate

    def recommendation_url(self):
        return '/api/v1/recommendations/players/'

    def query(self, **extra):
        params = {'sport': self.sport.id, 'latitude': 12.9716, 'longitude': 77.5946, 'radius': 20, 'limit': 10}
        params.update(extra)
        return self.client.get(self.recommendation_url(), params)

    def test_same_sport_skill_distance_availability_and_sorting(self):
        response = self.query()
        self.assertEqual(response.status_code, 200)
        results = response.data['results']
        self.assertEqual(results[0]['player']['username'], 'same')
        self.assertEqual(results[0]['score'], 90)
        self.assertIn('Same sport', results[0]['matching_reasons'])
        self.assertIn('Similar skill level', results[0]['matching_reasons'])
        self.assertIn('Compatible availability', results[0]['matching_reasons'])
        close = next(result for result in results if result['player']['username'] == 'close-skill')
        self.assertEqual(close['score'], 76)

    def test_different_sport_excluded_and_blocks_excluded(self):
        UserBlock.objects.create(user=self.requester, blocked_user=self.close_skill)
        response = self.query()
        usernames = [result['player']['username'] for result in response.data['results']]
        self.assertIn('same', usernames)
        self.assertNotIn('other-sport', usernames)
        self.assertNotIn('close-skill', usernames)

    def test_invalid_parameters_and_requester_without_sport(self):
        invalid = self.query(latitude=100)
        self.assertEqual(invalid.status_code, 400)
        UserSport.objects.filter(user=self.requester, sport=self.sport).delete()
        unavailable = self.query()
        self.assertEqual(unavailable.status_code, 400)


class VenueManagementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='reviewer', email='reviewer@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.user)
        self.other = User.objects.create_user(username='other-reviewer', email='other@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.other)
        self.sport = Sport.objects.get(name='Badminton')
        self.venue = Venue.objects.create(
            name='Arena One', description='Indoor courts', address='1 Main Road', city='Bengaluru',
            latitude=12.9716, longitude=77.5946, rating=4.5, opening_time='06:00', closing_time='22:00',
        )
        VenueSport.objects.create(venue=self.venue, sport=self.sport)
        VenueAmenity.objects.create(venue=self.venue, parking=True, washroom=True, lighting=True)
        self.court = Court.objects.create(venue=self.venue, sport=self.sport, name='Court A', capacity=4, price_per_hour=400)
        self.client.force_authenticate(self.user)

    def test_venue_and_court_creation_and_discovery_filters(self):
        self.assertEqual(self.venue.courts.count(), 1)
        filtered = self.client.get('/api/v1/venues/search/', {'sport': 'badminton', 'city': 'Bengaluru', 'min_rating': 4, 'max_price': 500, 'amenities': 'parking,lighting'})
        nearby = self.client.get('/api/v1/venues/nearby/', {'latitude': 12.9716, 'longitude': 77.5946, 'radius': 2, 'sport': 'badminton'})
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.data['results'][0]['name'], 'Arena One')
        self.assertEqual(nearby.status_code, 200)
        self.assertEqual(nearby.data['results'][0]['name'], 'Arena One')

    def test_review_eligibility_duplicate_and_deletion_permissions(self):
        completed = CourtBooking.objects.create(user=self.user, court=self.court, starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(hours=1), status='completed')
        cancelled = CourtBooking.objects.create(user=self.user, court=self.court, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1), status='cancelled')
        review = self.client.post('/api/v1/venues/reviews/', {'booking_id': completed.id, 'rating': 5, 'comment': 'Great'}, format='json')
        duplicate = self.client.post('/api/v1/venues/reviews/', {'booking_id': completed.id, 'rating': 5}, format='json')
        cancelled_response = self.client.post('/api/v1/venues/reviews/', {'booking_id': cancelled.id, 'rating': 4}, format='json')
        self.assertEqual(review.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(cancelled_response.status_code, 400)
        self.client.force_authenticate(self.other)
        forbidden = self.client.delete(f"/api/v1/venues/reviews/{review.data['review']['public_id']}/")
        self.assertEqual(forbidden.status_code, 403)
        self.client.force_authenticate(self.user)
        deleted = self.client.delete(f"/api/v1/venues/reviews/{review.data['review']['public_id']}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(VenueReview.objects.exists())


class VenueAvailabilityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='slot-user', email='slot@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.user)
        self.sport = Sport.objects.get(name='Badminton')
        self.venue = Venue.objects.create(
            name='Slots Arena', address='2 Main Road', city='Bengaluru', latitude=12.97, longitude=77.59,
            opening_time='09:00', closing_time='12:00', timezone='Asia/Kolkata',
        )
        self.court = Court.objects.create(venue=self.venue, sport=self.sport, name='Slot Court', capacity=4, price_per_hour=600)
        self.date = timezone.localdate() + timedelta(days=2)
        self.venue.refresh_from_db()
        self.open_at, _ = VenueAvailabilityService.local_range(self.venue, self.date)
        self.client.force_authenticate(self.user)

    def slots(self, **params):
        defaults = {'date': self.date.isoformat(), 'court': self.court.id, 'duration': 60}
        defaults.update(params)
        return self.client.get(f'/api/v1/venues/{self.venue.public_id}/availability/', defaults)

    def test_normal_slots_boundary_times_and_timezone(self):
        response = self.slots()
        self.assertEqual(response.status_code, 200)
        slots = response.data['slots']
        self.assertEqual(len(slots), 3)
        self.assertEqual(str(slots[0]['start_time']), '09:00:00')
        self.assertEqual(str(slots[-1]['end_time']), '12:00:00')
        self.assertTrue(all(slot['status'] == 'available' for slot in slots))
        self.assertEqual(response.data['timezone'], 'Asia/Kolkata')

    def test_booked_overlap_adjacent_and_blocked_statuses(self):
        CourtBooking.objects.create(user=self.user, court=self.court, starts_at=self.open_at + timedelta(minutes=30), ends_at=self.open_at + timedelta(minutes=90), status='confirmed')
        CourtBlockedPeriod.objects.create(court=self.court, starts_at=self.open_at + timedelta(hours=2), ends_at=self.open_at + timedelta(hours=3), type='maintenance')
        slots = self.slots().data['slots']
        self.assertEqual([slot['status'] for slot in slots], ['booked', 'booked', 'maintenance'])
        # An adjacent slot is permitted because it does not satisfy either overlap condition.
        booking = self.client.post('/api/v1/bookings/', {
            'venue_id': self.venue.id, 'court_id': self.court.id, 'booking_date': self.date.isoformat(),
            'start_time': '10:30', 'end_time': '11:00',
        }, format='json')
        self.assertEqual(booking.status_code, 201)

    def test_invalid_duration_closed_venue_and_transactional_conflict(self):
        invalid = self.slots(duration=45)
        self.assertEqual(invalid.status_code, 400)
        self.venue.active = False
        self.venue.save()
        closed = self.slots()
        self.assertTrue(all(slot['status'] == 'closed' for slot in closed.data['slots']))
        self.venue.active = True
        self.venue.save()
        first = self.client.post('/api/v1/bookings/', {
            'venue_id': self.venue.id, 'court_id': self.court.id, 'booking_date': self.date.isoformat(), 'start_time': '09:00', 'end_time': '10:00',
        }, format='json')
        second = self.client.post('/api/v1/bookings/', {
            'venue_id': self.venue.id, 'court_id': self.court.id, 'booking_date': self.date.isoformat(), 'start_time': '09:30', 'end_time': '10:30',
        }, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)


class BookingEngineTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='booker', email='booker@example.com', password='StrongPass!123')
        self.other = User.objects.create_user(username='booker-other', email='booker-other@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.user)
        PlayerProfile.objects.create(user=self.other)
        sport = Sport.objects.get(name='Badminton')
        self.venue = Venue.objects.create(name='Booking Arena', address='3 Road', city='Bengaluru', latitude=12.97, longitude=77.59, opening_time='09:00', closing_time='20:00')
        self.court = Court.objects.create(venue=self.venue, sport=sport, name='Booking Court', capacity=4, price_per_hour=500)
        self.other_venue = Venue.objects.create(name='Other Arena', address='4 Road', city='Bengaluru', latitude=12.98, longitude=77.60, opening_time='09:00', closing_time='20:00')
        self.other_court = Court.objects.create(venue=self.other_venue, sport=sport, name='Other Court', capacity=4, price_per_hour=500)
        self.date = timezone.localdate() + timedelta(days=3)
        self.client.force_authenticate(self.user)

    def payload(self, **overrides):
        data = {'venue_id': self.venue.id, 'court_id': self.court.id, 'booking_date': self.date.isoformat(), 'start_time': '10:00', 'end_time': '11:00'}
        data.update(overrides)
        return data

    def create(self, **overrides):
        return self.client.post('/api/v1/bookings/', self.payload(**overrides), format='json')

    def test_success_price_is_server_controlled_and_detail_is_private(self):
        response = self.create(final_amount='1')
        self.assertEqual(response.status_code, 201)
        data = response.data['data']
        self.assertEqual(str(data['final_amount']), '500.00')
        self.assertTrue(data['booking_reference'].startswith('SM-'))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/bookings/{data['public_id']}/").status_code, 404)

    def test_conflict_overlap_cancellation_and_slot_reuse(self):
        first = self.create()
        self.assertEqual(first.status_code, 201)
        conflict = self.create(start_time='10:30', end_time='11:30')
        self.assertEqual(conflict.status_code, 409)
        self.client.force_authenticate(self.other)
        forbidden = self.client.post(f"/api/v1/bookings/{first.data['data']['public_id']}/cancel/", {'reason': 'x'}, format='json')
        self.assertEqual(forbidden.status_code, 403)
        self.client.force_authenticate(self.user)
        cancelled = self.client.post(f"/api/v1/bookings/{first.data['data']['public_id']}/cancel/", {'reason': 'Plans changed'}, format='json')
        self.assertEqual(cancelled.status_code, 200)
        self.client.force_authenticate(self.other)
        reuse = self.client.post('/api/v1/bookings/', self.payload(), format='json')
        self.assertEqual(reuse.status_code, 201)

    def test_invalid_court_time_hours_past_date_and_completion(self):
        self.assertEqual(self.create(court_id=999999).status_code, 404)
        self.assertEqual(self.create(court_id=self.other_court.id).status_code, 400)
        self.assertEqual(self.create(start_time='11:00', end_time='10:00').status_code, 400)
        self.assertEqual(self.create(start_time='08:00', end_time='09:00').status_code, 400)
        self.assertEqual(self.create(booking_date=(timezone.localdate() - timedelta(days=1)).isoformat()).status_code, 400)
        created = self.create(start_time='12:00', end_time='13:00')
        booking = CourtBooking.objects.get(public_id=created.data['data']['public_id'])
        booking.starts_at = timezone.now() - timedelta(hours=2)
        booking.ends_at = timezone.now() - timedelta(minutes=1)
        booking.save(update_fields=['starts_at', 'ends_at'])
        self.assertEqual(BookingStatusService.complete_expired(), 1)
        booking.refresh_from_db()
        self.assertEqual(booking.status, CourtBooking.Status.COMPLETED)

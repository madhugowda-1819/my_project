from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
from rest_framework.test import APITestCase

from .models import Ground, Match, PlayerProfile, Sport, UserSport, UserBlock, Venue, VenueSport, Court, VenueAmenity, CourtBooking, VenueReview, CourtBlockedPeriod, Game, GamePlayer, Conversation, ConversationMember, Message, Community, CommunityMember, CommunityPostLike, Event, EventParticipant, Tournament, Notification, UserDevice, RecommendationProfile, Report, ModerationAction
from .services.locations import nearby_queryset
from .services.player_matching import PlayerMatchingService
from .services.availability import VenueAvailabilityService
from .services.bookings import BookingStatusService
from .services.game_matching import GameMatchingService
from .services.events import EventRegistrationService, TournamentBracketService, TournamentService
from .services.recommendation_engine import RecommendationRankingService

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
        rotated_refresh = token_response.data.get('refresh', refresh)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['tokens']['access']}")
        profile = self.client.get('/api/v1/users/me/')
        update = self.client.patch('/api/v1/users/me/', {'name': 'Asha Updated', 'city': 'Bengaluru', 'bio': 'Runner'}, format='json')
        logout = self.client.post('/api/v1/auth/logout/', {'refresh': rotated_refresh}, format='json')
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
            'venue_id': str(self.venue.public_id), 'court_id': str(self.court.public_id), 'booking_date': self.date.isoformat(),
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
            'venue_id': str(self.venue.public_id), 'court_id': str(self.court.public_id), 'booking_date': self.date.isoformat(), 'start_time': '09:00', 'end_time': '10:00',
        }, format='json')
        second = self.client.post('/api/v1/bookings/', {
            'venue_id': str(self.venue.public_id), 'court_id': str(self.court.public_id), 'booking_date': self.date.isoformat(), 'start_time': '09:30', 'end_time': '10:30',
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
        data = {'venue_id': str(self.venue.public_id), 'court_id': str(self.court.public_id), 'booking_date': self.date.isoformat(), 'start_time': '10:00', 'end_time': '11:00'}
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

    def test_booking_quote_uses_public_uuids_and_server_price(self):
        response = self.client.post('/api/v1/bookings/quote/', self.payload(final_amount='1'), format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['venue_name'], 'Booking Arena')
        self.assertEqual(response.data['court_name'], 'Booking Court')
        self.assertEqual(str(response.data['final_amount']), '500.00')

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
        self.assertEqual(self.create(court_id='00000000-0000-0000-0000-000000000000').status_code, 404)
        self.assertEqual(self.create(court_id=str(self.other_court.public_id)).status_code, 400)
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


class GameApiTests(APITestCase):
    def setUp(self):
        self.host = User.objects.create_user(username='game-host', email='game-host@example.com', password='StrongPass!123')
        self.player = User.objects.create_user(username='game-player', email='game-player@example.com', password='StrongPass!123')
        self.other = User.objects.create_user(username='game-other', email='game-other@example.com', password='StrongPass!123')
        for user in (self.host, self.player, self.other):
            PlayerProfile.objects.create(user=user)
        self.sport = Sport.objects.get(name='Badminton')
        for user in (self.host, self.player, self.other):
            UserSport.objects.create(user=user, sport=self.sport, skill_level='intermediate')
        self.venue = Venue.objects.create(
            name='Game Arena', address='5 Game Road', city='Bengaluru', latitude=12.9716, longitude=77.5946,
            opening_time='09:00', closing_time='20:00', timezone='Asia/Kolkata',
        )
        self.court = Court.objects.create(venue=self.venue, sport=self.sport, name='Game Court', capacity=2, price_per_hour=500)
        self.date = timezone.localdate() + timedelta(days=4)
        self.client.force_authenticate(self.host)

    def payload(self, **overrides):
        data = {
            'sport_id': self.sport.id, 'venue_id': str(self.venue.public_id), 'court_id': str(self.court.public_id),
            'game_date': self.date.isoformat(), 'start_time': '10:00', 'end_time': '11:00',
            'min_players': 2, 'max_players': 2, 'skill_level': 'intermediate', 'visibility': 'public',
        }
        data.update(overrides)
        return data

    def create_game(self, **overrides):
        return self.client.post('/api/v1/games/', self.payload(**overrides), format='json')

    def test_create_has_confirmed_host_and_prevents_court_conflicts(self):
        created = self.create_game()
        self.assertEqual(created.status_code, 201)
        game = Game.objects.get(public_id=created.data['data']['public_id'])
        self.assertEqual(game.game_players.filter(status='confirmed').count(), 1)
        self.assertEqual(game.host, self.host)
        overlapping = self.create_game(start_time='10:30', end_time='11:30')
        self.assertEqual(overlapping.status_code, 409)

    def test_join_capacity_leave_and_host_cancellation(self):
        created = self.create_game()
        game_id = created.data['data']['public_id']
        self.client.force_authenticate(self.player)
        joined = self.client.post(f'/api/v1/games/{game_id}/join/')
        self.assertEqual(joined.status_code, 200)
        self.assertEqual(joined.data['data']['status'], Game.Status.FULL)
        self.client.force_authenticate(self.other)
        full = self.client.post(f'/api/v1/games/{game_id}/join/')
        self.assertEqual(full.status_code, 409)
        self.client.force_authenticate(self.player)
        left = self.client.post(f'/api/v1/games/{game_id}/leave/')
        self.assertEqual(left.status_code, 200)
        self.assertEqual(GamePlayer.objects.get(game__public_id=game_id, user=self.player).status, GamePlayer.Status.LEFT)
        self.client.force_authenticate(self.host)
        cancelled = self.client.post(f'/api/v1/games/{game_id}/leave/')
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.data['status'], Game.Status.CANCELLED)

    def test_private_visibility_and_player_privacy(self):
        created = self.create_game(visibility='private')
        game_id = created.data['data']['public_id']
        self.client.force_authenticate(self.player)
        listing = self.client.get('/api/v1/games/')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data['count'], 0)
        denied = self.client.post(f'/api/v1/games/{game_id}/join/')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.host)
        players = self.client.get(f'/api/v1/games/{game_id}/players/')
        self.assertEqual(players.status_code, 200)
        self.assertNotIn('email', players.data['results'][0]['player'])
        self.assertNotIn('latitude', players.data['results'][0]['player'])


class GameMatchingServiceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='game-recommender', email='game-recommender@example.com', password='StrongPass!123',
            latitude=12.9716, longitude=77.5946,
        )
        PlayerProfile.objects.create(user=self.user, preferred_play_times=['evening'])
        self.host = User.objects.create_user(username='recommend-host', email='recommend-host@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.host)
        self.sport = Sport.objects.get(name='Badminton')
        self.other_sport = Sport.objects.get(name='Tennis')
        UserSport.objects.create(user=self.user, sport=self.sport, skill_level='intermediate', preferred=True)
        UserSport.objects.create(user=self.host, sport=self.sport, skill_level='intermediate')
        self.venue = Venue.objects.create(
            name='Recommendation Arena', address='6 Game Road', city='Bengaluru', latitude=12.972, longitude=77.595,
            opening_time='09:00', closing_time='22:00', timezone='Asia/Kolkata',
        )
        self.court = Court.objects.create(venue=self.venue, sport=self.sport, name='Recommendation Court', capacity=6, price_per_hour=500)
        self.date = timezone.localdate() + timedelta(days=5)
        self.client.force_authenticate(self.user)

    def make_game(self, **overrides):
        starts = timezone.now() + timedelta(days=5)
        values = {
            'game_reference': f'GM-TEST-{Game.objects.count() + 1}', 'host': self.host, 'sport': self.sport,
            'venue': self.venue, 'court': self.court, 'game_date': self.date,
            'start_time': '18:00', 'end_time': '19:00', 'starts_at': starts, 'ends_at': starts + timedelta(hours=1),
            'min_players': 2, 'max_players': 4, 'skill_level': 'intermediate', 'visibility': 'public', 'status': 'open',
        }
        values.update(overrides)
        game = Game.objects.create(**values)
        GamePlayer.objects.create(game=game, user=self.host)
        return game

    def test_preferred_sport_scores_and_returns_reasons(self):
        game = self.make_game()
        results = GameMatchingService.get_recommendations(user=self.user, latitude=12.9716, longitude=77.5946, radius=10)
        self.assertEqual(results[0]['game'], game)
        self.assertGreater(results[0]['match_score'], 70)
        self.assertIn('Matches your preferred sport', results[0]['reasons'])
        self.assertIn('Skill level is a strong match', results[0]['reasons'])

    def test_excludes_full_private_cancelled_and_already_joined_games(self):
        available = self.make_game()
        full = self.make_game(game_reference='GM-FULL', min_players=1, max_players=1)
        private = self.make_game(game_reference='GM-PRIVATE', visibility='private')
        cancelled = self.make_game(game_reference='GM-CANCELLED', status='cancelled')
        joined = self.make_game(game_reference='GM-JOINED')
        GamePlayer.objects.create(game=joined, user=self.user)
        ids = [item['game'].id for item in GameMatchingService.get_recommendations(user=self.user, latitude=12.9716, longitude=77.5946, radius=10)]
        self.assertIn(available.id, ids)
        self.assertNotIn(full.id, ids)
        self.assertNotIn(private.id, ids)
        self.assertNotIn(cancelled.id, ids)
        self.assertNotIn(joined.id, ids)

    def test_endpoint_saved_location_limit_and_no_sport_returns_empty(self):
        self.make_game()
        response = self.client.get('/api/v1/recommendations/games/', {'limit': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 1)
        UserSport.objects.filter(user=self.user).delete()
        empty = self.client.get('/api/v1/recommendations/games/')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data['data'], [])


class ConversationChatTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='chat-user', email='chat-user@example.com', password='StrongPass!123')
        self.other = User.objects.create_user(username='chat-other', email='chat-other@example.com', password='StrongPass!123')
        self.outsider = User.objects.create_user(username='chat-outsider', email='chat-outsider@example.com', password='StrongPass!123')
        for user in (self.user, self.other, self.outsider):
            PlayerProfile.objects.create(user=user)
        self.client.force_authenticate(self.user)

    def create_direct(self):
        return self.client.post('/api/v1/conversations/', {'type': 'ONE_TO_ONE', 'user_id': self.other.id}, format='json')

    def test_create_reuse_send_read_edit_and_soft_delete(self):
        created = self.create_direct()
        reused = self.create_direct()
        self.assertEqual(created.status_code, 201)
        self.assertEqual(reused.status_code, 200)
        conversation_id = created.data['data']['public_id']
        self.assertEqual(Conversation.objects.count(), 1)
        sent = self.client.post(f'/api/v1/conversations/{conversation_id}/messages/', {'content': '  Ready for badminton?  '}, format='json')
        self.assertEqual(sent.status_code, 201)
        message_id = sent.data['data']['public_id']
        self.client.force_authenticate(self.other)
        listed = self.client.get(f'/api/v1/conversations/{conversation_id}/messages/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['count'], 1)
        self.assertEqual(self.client.post(f'/api/v1/conversations/{conversation_id}/read/').status_code, 200)
        self.client.force_authenticate(self.user)
        edited = self.client.patch(f'/api/v1/messages/{message_id}/', {'content': 'Updated plan'}, format='json')
        self.assertEqual(edited.status_code, 200)
        self.assertTrue(edited.data['data']['is_edited'])
        deleted = self.client.delete(f'/api/v1/messages/{message_id}/')
        self.assertEqual(deleted.status_code, 200)
        message = Message.objects.get(public_id=message_id)
        self.assertIsNotNone(message.deleted_at)
        self.assertEqual(message.content, '')

    def test_permissions_validation_and_blocking(self):
        created = self.create_direct()
        conversation_id = created.data['data']['public_id']
        empty = self.client.post(f'/api/v1/conversations/{conversation_id}/messages/', {'content': '   '}, format='json')
        self.assertEqual(empty.status_code, 400)
        self.client.force_authenticate(self.outsider)
        forbidden = self.client.get(f'/api/v1/conversations/{conversation_id}/messages/')
        self.assertEqual(forbidden.status_code, 403)
        self.client.force_authenticate(self.user)
        UserBlock.objects.create(user=self.user, blocked_user=self.other)
        blocked = self.client.post('/api/v1/conversations/', {'type': 'ONE_TO_ONE', 'user_id': self.other.id}, format='json')
        self.assertEqual(blocked.status_code, 403)

    def test_game_conversation_only_allows_confirmed_players(self):
        sport = Sport.objects.get(name='Badminton')
        venue = Venue.objects.create(name='Chat Arena', address='7 Road', city='Bengaluru', latitude=12.97, longitude=77.59, opening_time='09:00', closing_time='20:00')
        court = Court.objects.create(venue=venue, sport=sport, name='Chat Court', capacity=4, price_per_hour=500)
        starts = timezone.now() + timedelta(days=2)
        game = Game.objects.create(game_reference='GM-CHAT', host=self.user, sport=sport, venue=venue, court=court, game_date=timezone.localdate() + timedelta(days=2), start_time='10:00', end_time='11:00', starts_at=starts, ends_at=starts + timedelta(hours=1), min_players=2, max_players=4)
        GamePlayer.objects.create(game=game, user=self.user)
        GamePlayer.objects.create(game=game, user=self.other)
        group = self.client.post('/api/v1/conversations/', {'type': 'GAME_GROUP', 'game_id': str(game.public_id)}, format='json')
        self.assertEqual(group.status_code, 200)
        conversation_id = group.data['data']['public_id']
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f'/api/v1/conversations/{conversation_id}/').status_code, 404)
        self.client.force_authenticate(self.other)
        sent = self.client.post(f'/api/v1/conversations/{conversation_id}/messages/', {'content': 'I will be there.'}, format='json')
        self.assertEqual(sent.status_code, 201)


class CommunityApiTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='community-owner', email='community-owner@example.com', password='StrongPass!123', city='Bengaluru', latitude=12.9716, longitude=77.5946)
        self.member = User.objects.create_user(username='community-member', email='community-member@example.com', password='StrongPass!123')
        self.other = User.objects.create_user(username='community-other', email='community-other@example.com', password='StrongPass!123')
        for user in (self.owner, self.member, self.other):
            PlayerProfile.objects.create(user=user)
        self.sport = Sport.objects.get(name='Badminton')
        UserSport.objects.create(user=self.owner, sport=self.sport, preferred=True)
        self.client.force_authenticate(self.owner)

    def create(self, **changes):
        payload = {'name': 'Bengaluru Badminton Club', 'sport_id': self.sport.id, 'city': 'Bengaluru', 'latitude': 12.972, 'longitude': 77.595, 'visibility': 'public'}
        payload.update(changes)
        return self.client.post('/api/v1/communities/', payload, format='json')

    def test_create_owner_membership_join_post_comment_and_like(self):
        created = self.create()
        self.assertEqual(created.status_code, 201)
        community_id = created.data['data']['public_id']
        community = Community.objects.get(public_id=community_id)
        self.assertEqual(community.member_count, 1)
        self.assertEqual(community.members.get(user=self.owner).role, CommunityMember.Role.OWNER)
        self.client.force_authenticate(self.member)
        self.assertTrue(self.client.post(f'/api/v1/communities/{community_id}/join/').data['joined'])
        post = self.client.post(f'/api/v1/communities/{community_id}/posts/', {'content': 'Looking for an evening game.'}, format='json')
        self.assertEqual(post.status_code, 201)
        post_id = post.data['data']['public_id']
        comment = self.client.post(f'/api/v1/community-posts/{post_id}/comments/', {'content': 'I can join.'}, format='json')
        self.assertEqual(comment.status_code, 201)
        like = self.client.post(f'/api/v1/community-posts/{post_id}/like/')
        duplicate = self.client.post(f'/api/v1/community-posts/{post_id}/like/')
        self.assertEqual(like.status_code, 201)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(CommunityPostLike.objects.count(), 1)

    def test_private_request_approval_and_ban(self):
        created = self.create(name='Private Club', visibility='private')
        community_id = created.data['data']['public_id']
        self.client.force_authenticate(self.member)
        requested = self.client.post(f'/api/v1/communities/{community_id}/join/')
        self.assertTrue(requested.data['pending'])
        duplicate = self.client.post(f'/api/v1/communities/{community_id}/join/')
        self.assertFalse(duplicate.data['changed'])
        self.client.force_authenticate(self.owner)
        approved = self.client.post(f'/api/v1/communities/{community_id}/join-requests/{self.member.id}/approve/')
        self.assertEqual(approved.status_code, 200)
        banned = self.client.post(f'/api/v1/communities/{community_id}/members/{self.member.id}/ban/')
        self.assertEqual(banned.status_code, 200)
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.post(f'/api/v1/communities/{community_id}/join/').status_code, 403)


class EventTournamentTests(APITestCase):
    def setUp(self):
        self.organizer = User.objects.create_user(username='event-organizer', email='event-organizer@example.com', password='StrongPass!123')
        self.first = User.objects.create_user(username='event-first', email='event-first@example.com', password='StrongPass!123')
        self.second = User.objects.create_user(username='event-second', email='event-second@example.com', password='StrongPass!123')
        self.sport = Sport.objects.get(name='Badminton')
        for user in (self.organizer, self.first, self.second):
            PlayerProfile.objects.create(user=user)
            UserSport.objects.create(user=user, sport=self.sport, skill_level='intermediate')
        self.venue = Venue.objects.create(name='Event Arena', address='8 Road', city='Bengaluru', latitude=12.97, longitude=77.59, opening_time='09:00', closing_time='20:00')
        self.court = Court.objects.create(venue=self.venue, sport=self.sport, name='Event Court', capacity=8, price_per_hour=500)
        self.client.force_authenticate(self.organizer)

    def create_event(self):
        date = timezone.localdate() + timedelta(days=7)
        return self.client.post('/api/v1/events/', {
            'title': 'Weekend Event', 'sport_id': self.sport.id, 'venue_id': str(self.venue.public_id),
            'court_id': str(self.court.public_id), 'event_date': date.isoformat(), 'start_time': '10:00', 'end_time': '11:00',
            'maximum_participants': 1, 'minimum_participants': 1,
            'registration_deadline': (timezone.now() + timedelta(days=6)).isoformat(),
        }, format='json')

    def test_event_registration_waitlist_and_promotion(self):
        created = self.create_event()
        self.assertEqual(created.status_code, 201)
        event = Event.objects.get(public_id=created.data['data']['public_id'])
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.post(f'/api/v1/events/{event.public_id}/register/').data['data']['status'], 'registered')
        self.client.force_authenticate(self.second)
        self.assertEqual(self.client.post(f'/api/v1/events/{event.public_id}/register/').data['data']['status'], 'waitlisted')
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.post(f'/api/v1/events/{event.public_id}/leave/').status_code, 200)
        self.assertEqual(EventParticipant.objects.get(event=event, user=self.second).status, EventParticipant.Status.REGISTERED)

    def test_tournament_team_and_bracket_generation(self):
        start = timezone.localdate() + timedelta(days=10)
        tournament = Tournament.objects.create(
            tournament_reference='TR-TEST', name='Test Tournament', organizer=self.organizer, sport=self.sport,
            venue=self.venue, start_date=start, end_date=start + timedelta(days=1),
            registration_deadline=timezone.now() + timedelta(days=8), maximum_teams=4,
        )
        first = TournamentService.create_team(tournament=tournament, creator=self.organizer, name='Alpha')
        # A staff organizer is not needed because the tournament owner creates both teams.
        second = TournamentService.create_team(tournament=tournament, creator=self.organizer, name='Beta')
        matches = TournamentBracketService.generate(tournament=tournament, actor=self.organizer)
        self.assertEqual(len(matches), 1)
        self.assertEqual({matches[0].team_a_id, matches[0].team_b_id}, {first.id, second.id})


class NotificationApiTests(APITestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='notify-user',email='notify-user@example.com',password='StrongPass!123')
        self.other=User.objects.create_user(username='notify-other',email='notify-other@example.com',password='StrongPass!123')
        self.notification=Notification.objects.create(user=self.user,type='message',title='Hello',body='World')
        self.client.force_authenticate(self.user)
    def test_read_count_preferences_and_device(self):
        self.assertEqual(self.client.get('/api/v1/notifications/unread-count/').data['unread_count'],1)
        self.assertEqual(self.client.post(f'/api/v1/notifications/{self.notification.public_id}/read/').status_code,200)
        self.assertTrue(Notification.objects.get(pk=self.notification.pk).is_read)
        self.assertEqual(self.client.patch('/api/v1/notifications/preferences/',{'chat_notifications':False},format='json').status_code,200)
        registered=self.client.post('/api/v1/notifications/devices/',{'device_token':'test-device-token','platform':'android'},format='json')
        self.assertEqual(registered.status_code,201)
        self.assertEqual(self.client.delete(f"/api/v1/notifications/devices/{registered.data['data']['public_id']}/").status_code,204)
    def test_notification_ownership(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/v1/notifications/{self.notification.public_id}/').status_code,404)


class RecommendationEngineTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='recommend-user', email='recommend-user@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.user, preferred_play_times=['evening'])
        self.client.force_authenticate(self.user)

    def test_cold_start_dashboard_is_safe_and_server_derived(self):
        response = self.client.get('/api/v1/recommendations/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['algorithm'], 'deterministic_hybrid_v1')
        self.assertEqual(response.data['data']['profile']['preferred_play_times'], ['evening'])
        self.assertTrue(RecommendationProfile.objects.filter(user=self.user).exists())

    def test_ranking_is_deterministic_and_bounded(self):
        self.assertEqual(RecommendationRankingService.venue_score(supports_sport=True, rating=5, distance_km=1), 100)
        self.assertEqual(RecommendationRankingService.venue_score(supports_sport=False, rating=0, distance_km=100), 0)


class ModerationApiTests(APITestCase):
    def setUp(self):
        self.reporter = User.objects.create_user(username='reporter', email='reporter@example.com', password='StrongPass!123')
        self.target = User.objects.create_user(username='target', email='target@example.com', password='StrongPass!123')
        self.moderator = User.objects.create_user(username='moderator', email='moderator@example.com', password='StrongPass!123')
        self.moderator.groups.add(Group.objects.create(name='SportMate Moderators'))
        self.admin = User.objects.create_user(username='platform-admin', email='platform-admin@example.com', password='StrongPass!123')
        self.admin.groups.add(Group.objects.create(name='SportMate Admins'))

    def create_report(self):
        self.client.force_authenticate(self.reporter)
        return self.client.post('/api/v1/reports/', {
            'target_type': 'user', 'target_public_id': str(self.target.public_id),
            'reason': 'abuse', 'description': 'Repeated abusive conduct.',
        }, format='json')

    def test_report_ownership_moderation_and_restore(self):
        created = self.create_report()
        self.assertEqual(created.status_code, 201)
        report_id = created.data['data']['public_id']
        self.assertEqual(self.client.get('/api/v1/admin/dashboard/').status_code, 403)
        self.client.force_authenticate(self.moderator)
        suspended = self.client.patch(f'/api/v1/reports/{report_id}/', {'action': 'suspend', 'action_reason': 'Policy violation.'}, format='json')
        self.assertEqual(suspended.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_status, User.AccountStatus.SUSPENDED)
        self.assertEqual(ModerationAction.objects.filter(action='suspend').count(), 1)
        restored = self.client.patch(f'/api/v1/reports/{report_id}/', {'action': 'restore', 'status': 'resolved', 'resolution': 'Restriction lifted.'}, format='json')
        self.assertEqual(restored.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_status, User.AccountStatus.ACTIVE)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/v1/admin/dashboard/').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/admin/moderation/actions/').status_code, 200)

    def test_normal_user_cannot_access_another_report(self):
        created = self.create_report()
        report_id = created.data['data']['public_id']
        self.client.force_authenticate(self.target)
        self.assertEqual(self.client.get(f'/api/v1/reports/{report_id}/').status_code, 404)


class ProductionHardeningTests(APITestCase):
    def test_health_endpoint_is_non_sensitive(self):
        response = self.client.get('/api/v1/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'status': 'ok'})

    def test_profile_update_cannot_escalate_account_fields(self):
        user = User.objects.create_user(username='harden-user', email='harden@example.com', password='StrongPass!123')
        self.client.force_authenticate(user)
        response = self.client.patch('/api/v1/users/me/', {
            'full_name': 'Safe Name', 'is_staff': True, 'account_status': 'deactivated',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.full_name, 'Safe Name')
        self.assertFalse(user.is_staff)
        self.assertEqual(user.account_status, User.AccountStatus.ACTIVE)

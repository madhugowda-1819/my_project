from django.test import TestCase
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.utils import timezone
from django.core.cache import cache
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from rest_framework.test import APITestCase
from rest_framework.exceptions import ValidationError

from .models import Ground, SportsGround, Match, MatchParticipation, MatchReplacementOpportunity, MatchReplacementInvitation, MatchReplacementHistory, PracticeActivity, PracticeCompletion, SportsGoal, HealthActivityRecord, NotificationPreference, PlayerProfile, Sport, AvailabilitySlot, UserSport, UserBlock, Venue, VenueSport, Court, VenueAmenity, CourtBooking, VenueReview, CourtBlockedPeriod, Game, GamePlayer, Conversation, ConversationMember, Message, Community, CommunityMember, CommunityPostLike, Event, EventParticipant, EventReminder, Tournament, Notification, UserDevice, RecommendationProfile, PlayerRating, MatchReflection, ReferralAttribution, College, CollegeMembership, CollegeChallenge, TournamentTeam, TournamentTeamMember, TournamentMatch, CollegeResultDispute, MatchTimePoll, MatchTimePollOption, MatchTimePollVote, Achievement, UserAchievement, SportChallenge, ChallengeEntry, ChallengeRewardGrant
from .maps import find_live_sports_grounds
from .services.matches import accept_rescue_invitation
from .services.locations import nearby_queryset
from .services.ground_ingestion import GroundIngestionService
from .services.player_matching import PlayerMatchingService
from .services.availability import VenueAvailabilityService
from .services.bookings import BookingConflict, BookingService, BookingStatusService
from .services.game_matching import GameMatchingService, apply_mood_ranking, mood_compatibility
from .services.events import EventRegistrationService, EventReminderService, TournamentBracketService, TournamentMatchService, TournamentService, TournamentStandingsService
from .services.colleges import CollegeService
from .services.match_time_polls import MatchTimePollService
from .services.practice import PracticeService
from .services.goals import SportsGoalService
from .services.training_recommendations import build_training_plan
from .services.training_recommendations import build_training_plan
from .services.health import HealthActivityService
from .services.recommendation_engine import RecommendationRankingService
from .services.referrals import ReferralService
from .services.achievements import AchievementEvaluationService

User = get_user_model()


class LiveMapsAdapterTests(SimpleTestCase):
    @override_settings(GOOGLE_MAPS_PLACES_API_KEY='test-server-key')
    @patch('my_app.maps.cache.set')
    @patch('my_app.maps.cache.get', return_value=None)
    @patch('my_app.maps.urlopen')
    def test_google_text_search_is_fresh_and_omits_unnamed_results(
            self, urlopen, cache_get, cache_set):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = (
            b'{"places":['
            b'{"id":"abc","displayName":{"text":"Machaxi Sports Center"},'
            b'"formattedAddress":"Bengaluru","location":{"latitude":12.97,"longitude":77.59},'
            b'"websiteUri":"https://example.test"},'
            b'{"id":"unnamed","location":{"latitude":12.97,"longitude":77.59}}]}'
        )
        results = find_live_sports_grounds(
            latitude=12.97, longitude=77.59, query='Machaxi Sports Center',
            sport='badminton', radius_km=5,
        )
        self.assertEqual([item['name'] for item in results], ['Machaxi Sports Center'])
        self.assertEqual(results[0]['source'], 'google_maps_live')
        self.assertNotIn('rating', results[0])
        cache_get.assert_not_called()
        cache_set.assert_not_called()
        request = urlopen.call_args.args[0]
        self.assertIn(b'Machaxi Sports Center badminton', request.data)
        self.assertNotIn(b'test-server-key', request.data)

    @override_settings(GOOGLE_MAPS_PLACES_API_KEY='')
    @patch('my_app.maps.cache.set')
    @patch('my_app.maps.cache.get', return_value=None)
    @patch('my_app.maps.urlopen')
    def test_osm_search_discards_features_without_real_names(
            self, urlopen, cache_get, cache_set):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = (
            b'{"elements":['
            b'{"type":"node","id":1,"lat":12.97,"lon":77.59,"tags":{"sport":"cricket"}},'
            b'{"type":"node","id":2,"lat":12.97,"lon":77.59,"tags":{"name":"Town Cricket Ground"}}]}'
        )
        results = find_live_sports_grounds(latitude=12.97, longitude=77.59)
        self.assertEqual([item['name'] for item in results], ['Town Cricket Ground'])
        self.assertEqual(results[0]['source'], 'openstreetmap_live')
        cache_set.assert_called_once()


class HealthActivityApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='health-user', email='health@example.com', password='pw123456')
        self.other = User.objects.create_user(username='health-other', email='health-other@example.com', password='pw123456')
        self.client.force_authenticate(self.user)
        # Manual health entries default to India local time; do not derive the
        # "future" fixture from Django's UTC localdate near a timezone boundary.
        self.today = timezone.now().astimezone(ZoneInfo('Asia/Kolkata')).date()

    def test_manual_activity_validation_and_summary_totals(self):
        for data in [
            {'metric': 'steps', 'value': 1000, 'recorded_date': self.today.isoformat()},
            {'metric': 'activity_duration', 'value': 30, 'recorded_date': self.today.isoformat()},
            {'metric': 'active_calories', 'value': 180, 'recorded_date': self.today.isoformat()},
        ]:
            response = self.client.post('/api/v1/health/activity/', data, format='json')
            self.assertEqual(response.status_code, 201, response.data)
        summary = self.client.get(f'/api/v1/health/activity/?start={self.today.isoformat()}&end={self.today.isoformat()}')
        totals = summary.data['data']['totals']
        self.assertEqual(totals['steps']['value'], 1000)
        self.assertEqual(totals['activity_duration']['value'], 30)
        self.assertEqual(totals['active_calories']['value'], 180)
        self.assertTrue(summary.data['data']['records'][0]['is_estimated'] or summary.data['data']['records'][1]['is_estimated'] or summary.data['data']['records'][2]['is_estimated'])
        fractional_steps = self.client.post('/api/v1/health/activity/', {'metric': 'steps', 'value': '2.5', 'recorded_date': self.today.isoformat()}, format='json')
        self.assertEqual(fractional_steps.status_code, 400)
        future = self.client.post('/api/v1/health/activity/', {'metric': 'steps', 'value': 3, 'recorded_date': (self.today+timedelta(days=1)).isoformat()}, format='json')
        self.assertEqual(future.status_code, 400)
        self.assertFalse(HealthActivityRecord.objects.filter(user=self.user, recorded_date=self.today + timedelta(days=1)).exists())

    def test_health_activity_rejects_malformed_and_missing_fields(self):
        malformed = self.client.post('/api/v1/health/activity/', {
            'metric': 'steps', 'value': 500, 'recorded_date': 'not-a-date',
        }, format='json')
        missing = self.client.post('/api/v1/health/activity/', {
            'metric': 'steps', 'value': 500,
        }, format='json')
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(HealthActivityRecord.objects.filter(user=self.user).count(), 0)

    def test_health_import_rejects_future_date_without_persisting(self):
        future_date = self.today + timedelta(days=1)
        response = self.client.post('/api/v1/health/activity/sync/', {
            'source': HealthActivityRecord.Source.HEALTH_CONNECT,
            'records': [{
                'metric': 'steps', 'value': 2500,
                'recorded_date': future_date.isoformat(),
                'external_record_id': 'future-record',
            }],
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(HealthActivityRecord.objects.filter(user=self.user, external_record_id='future-record').exists())

    def test_import_is_idempotent_and_records_are_private(self):
        record = {'metric': 'steps', 'value': 7000, 'recorded_date': self.today.isoformat(), 'external_record_id': 'hc-record-1', 'timezone': 'Asia/Kolkata'}
        payload = {'source': 'health_connect', 'records': [record, record]}
        first = self.client.post('/api/v1/health/activity/sync/', payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['data'], {'created': 1, 'updated': 1})
        self.assertEqual(HealthActivityRecord.objects.filter(user=self.user).count(), 1)
        repeated = self.client.post('/api/v1/health/activity/sync/', payload, format='json')
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.data['data'], {'created': 0, 'updated': 2})
        imported = HealthActivityRecord.objects.get(user=self.user, source='health_connect')
        detail = self.client.get(f'/api/v1/health/activity/{imported.public_id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['data']['external_record_id'], 'hc-record-1')
        self.client.force_authenticate(self.other)
        other_summary = self.client.get(f'/api/v1/health/activity/?start={self.today.isoformat()}&end={self.today.isoformat()}')
        self.assertEqual(other_summary.data['data']['records'], [])
        private_detail = self.client.get(f'/api/v1/health/activity/{imported.public_id}/')
        self.assertEqual(private_detail.status_code, 404)
        self.client.force_authenticate(self.user)
        manual = self.client.post('/api/v1/health/activity/', {'metric': 'steps', 'value': 100, 'recorded_date': self.today.isoformat()}, format='json')
        self.assertEqual(manual.status_code, 201)
        self.client.force_authenticate(self.other)
        private_record = self.client.delete(f"/api/v1/health/activity/{manual.data['data']['public_id']}/")
        self.assertEqual(private_record.status_code, 404)
        self.client.force_authenticate(self.user)
        own_delete = self.client.delete(f"/api/v1/health/activity/{manual.data['data']['public_id']}/")
        self.assertEqual(own_delete.status_code, 204)
        disconnected = self.client.post('/api/v1/health/activity/disconnect/', {'source': 'health_connect'}, format='json')
        self.assertEqual(disconnected.status_code, 200)
        self.assertEqual(HealthActivityRecord.objects.filter(user=self.user, source='health_connect').count(), 0)

    def test_activity_dates_use_the_requested_timezone_at_utc_boundary(self):
        boundary = timezone.make_aware(datetime(2026, 10, 15, 20, 0), ZoneInfo('UTC'))
        with patch('django.utils.timezone.now', return_value=boundary):
            india_date = '2026-10-16'
            accepted = self.client.post('/api/v1/health/activity/', {
                'metric': 'steps', 'value': 100,
                'recorded_date': india_date, 'timezone': 'Asia/Kolkata',
            }, format='json')
            utc_future = self.client.post('/api/v1/health/activity/', {
                'metric': 'steps', 'value': 100,
                'recorded_date': india_date, 'timezone': 'UTC',
            }, format='json')
        self.assertEqual(accepted.status_code, 201, accepted.data)
        self.assertEqual(utc_future.status_code, 400)


class SportsGoalApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='goal-user', email='goal@example.com', password='pw123456')
        self.other = User.objects.create_user(username='goal-other', email='goal-other@example.com', password='pw123456')
        self.client.force_authenticate(self.user)
        self.start = timezone.localdate() - timedelta(days=7)
        self.end = self.start + timedelta(days=21)

    def _create(self, **overrides):
        data = {'title': 'Practice this week', 'target': 3, 'unit': 'days', 'start_date': self.start.isoformat(), 'end_date': self.end.isoformat(), 'progress_source': 'practice_days'}
        data.update(overrides)
        return self.client.post('/api/v1/goals/', data, format='json')

    def test_calculated_practice_progress_is_traceable_and_goal_completes(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.data)
        goal_id = response.data['data']['public_id']
        activity = PracticeActivity.objects.create(user=self.user, name='Serve practice')
        for offset in (-2, -1, 0):
            PracticeCompletion.objects.create(user=self.user, activity=activity, activity_name=activity.name, practice_date=timezone.localdate() + timedelta(days=offset))
        self.assertEqual(PracticeCompletion.objects.filter(user=self.user).count(), 3)
        self.assertEqual(PracticeCompletion.objects.filter(user_id=self.user.id).count(), 3)
        self.assertEqual(PracticeCompletion.objects.values('practice_date').distinct().count(), 3)
        self.assertEqual(SportsGoalService.list_for(user=self.user)[0]['progress'], 3)
        goals = self.client.get('/api/v1/goals/')
        goal = goals.data['data'][0]
        self.assertEqual(goal['progress'], 3)
        self.assertEqual(goal['progress_mode'], 'calculated')
        self.assertEqual(goal['status'], 'completed')
        self.assertEqual(SportsGoal.objects.get(public_id=goal_id).status, SportsGoal.Status.COMPLETED)

    def test_completed_match_count_excludes_unfinished_and_manual_source_is_explicit(self):
        sport = Sport.objects.get(name='Football')
        ground = Ground.objects.create(name='Goal Ground', city='Bengaluru', latitude=12.9, longitude=77.6)
        goal_response = self._create(title='Play matches', target=2, unit='matches', progress_source='matches_completed')
        Match.objects.create(sport=sport, ground=ground, organizer=self.user, date_time=timezone.now()-timedelta(days=1), end_date_time=timezone.now()-timedelta(hours=23), total_players=2, status='completed')
        self.assertEqual(Match.objects.filter(organizer=self.user, status='completed').count(), 1)
        match_goals = self.client.get('/api/v1/goals/').data['data']
        self.assertEqual(next(goal for goal in match_goals if goal['title'] == 'Play matches')['progress'], 1)
        manual = self._create(title='Training minutes', target=60, unit='minutes', progress_source='manual', manual_progress=20)
        self.assertEqual(manual.status_code, 201)
        self.assertEqual(manual.data['data']['progress_mode'], 'manual')
        self.assertEqual(manual.data['data']['progress'], 20)

    def test_goal_ownership_dates_and_status_transitions_are_enforced(self):
        response = self._create()
        public_id = response.data['data']['public_id']
        self.client.force_authenticate(self.other)
        denied = self.client.patch(f'/api/v1/goals/{public_id}/', {'title': 'Not mine'}, format='json')
        self.assertEqual(denied.status_code, 404)
        self.client.force_authenticate(self.user)
        invalid = self._create(title='Wrong dates', end_date=(self.start-timedelta(days=1)).isoformat())
        self.assertEqual(invalid.status_code, 400)
        premature = self.client.post(f'/api/v1/goals/{public_id}/', {'status': 'completed'}, format='json')
        self.assertEqual(premature.status_code, 400)
        paused = self.client.post(f'/api/v1/goals/{public_id}/', {'status': 'paused'}, format='json')
        self.assertEqual(paused.status_code, 200)

    def test_manual_goal_progress_can_be_updated_and_update_rules_match_create(self):
        created = self._create(
            title='Training minutes', target=90, unit='minutes',
            progress_source='manual', manual_progress=10,
        )
        self.assertEqual(created.status_code, 201, created.data)
        public_id = created.data['data']['public_id']
        updated = self.client.patch(
            f'/api/v1/goals/{public_id}/', {'manual_progress': 45}, format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['data']['progress'], 45)

        over_target = self.client.patch(
            f'/api/v1/goals/{public_id}/', {'manual_progress': 91}, format='json',
        )
        self.assertEqual(over_target.status_code, 400)
        wrong_unit = self.client.patch(
            f'/api/v1/goals/{public_id}/', {'unit': 'days'}, format='json',
        )
        self.assertEqual(wrong_unit.status_code, 400)
        too_long = self.client.patch(
            f'/api/v1/goals/{public_id}/',
            {'end_date': (self.start + timedelta(days=367)).isoformat()}, format='json',
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(SportsGoal.objects.get(public_id=public_id).manual_progress, 45)

    @patch('my_app.services.goals.timezone.now')
    def test_due_reminder_is_scheduled_once_and_respects_existing_preferences(self, mocked_now):
        from datetime import datetime, time
        local_now = datetime.combine(self.start, time(19, 5), tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Kolkata'))
        mocked_now.return_value = local_now
        goal = SportsGoal.objects.create(user=self.user, title='Serve practice', target=3, unit='days', start_date=self.start, end_date=self.end, progress_source='practice_days', reminder_enabled=True, reminder_time=time(19), reminder_timezone='Asia/Kolkata')
        self.assertEqual(SportsGoalService.deliver_due_reminders(now=local_now), 1)
        self.assertEqual(SportsGoalService.deliver_due_reminders(now=local_now), 0)
        self.assertEqual(self.user.notifications.filter(title='Practice goal reminder').count(), 1)
        goal.reminder_last_sent_date = None
        goal.save(update_fields=['reminder_last_sent_date'])
        preference = NotificationPreference.objects.get(user=self.user)
        preference.event_notifications = False
        preference.save(update_fields=['event_notifications'])
        self.assertEqual(SportsGoalService.deliver_due_reminders(now=local_now), 0)


class TrainingRecommendationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='training-user', email='training@example.com', password='pw123456')
        self.sport = Sport.objects.get(name='Cricket')
        UserSport.objects.create(user=self.user, sport=self.sport, skill_level=UserSport.SkillLevel.BEGINNER)
        self.client.force_authenticate(self.user)

    def test_rule_based_plan_is_explainable_and_duration_is_allocated(self):
        activity = PracticeActivity.objects.create(user=self.user, name='Warm-up')
        PracticeCompletion.objects.create(user=self.user, activity=activity, activity_name='Warm-up', practice_date=timezone.localdate())
        SportsGoal.objects.create(user=self.user, title='Practice this week', target=3, unit='days',
                                  start_date=timezone.localdate(), end_date=timezone.localdate() + timedelta(days=7),
                                  progress_source=SportsGoal.Source.PRACTICE_DAYS)
        response = self.client.post('/api/v1/training/recommendations/', {
            'sport_id': self.sport.pk, 'goal': 'skill_development', 'duration_minutes': 45,
            'days_per_week': 3,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content.decode()[:300])
        plan = response.data['data']
        self.assertEqual(plan['method'], 'rule_based')
        self.assertEqual(plan['generated_for']['skill_level'], 'beginner')
        self.assertEqual(plan['warm_up']['duration_minutes'] + sum(d['duration_minutes'] for d in plan['drills']) + plan['cool_down']['duration_minutes'], 45)
        self.assertEqual(len(plan['weekly_plan']), 3)
        self.assertEqual(plan['recent_context']['checklist_completions_last_7_days'], 1)
        self.assertEqual(plan['recent_context']['active_goal_titles'], ['Practice this week'])
        self.assertIn('not medical advice', plan['disclaimer'])
        self.assertEqual(PracticeActivity.objects.filter(user=self.user).count(), 1)

    def test_invalid_inputs_and_unowned_sport_are_rejected(self):
        invalid = self.client.post('/api/v1/training/recommendations/', {
            'sport_id': self.sport.pk, 'goal': 'lose_weight', 'duration_minutes': 10,
        }, format='json')
        self.assertEqual(invalid.status_code, 400)
        other_sport = Sport.objects.get(name='Tennis')
        not_in_profile = self.client.post('/api/v1/training/recommendations/', {
            'sport_id': other_sport.pk, 'goal': 'general_fitness', 'duration_minutes': 30,
        }, format='json')
        self.assertEqual(not_in_profile.status_code, 400)

    def test_requires_active_authenticated_account(self):
        self.client.force_authenticate(user=None)
        response = self.client.post('/api/v1/training/recommendations/', {
            'sport_id': self.sport.pk, 'goal': 'general_fitness', 'duration_minutes': 30,
        }, format='json')
        self.assertEqual(response.status_code, 401)

    @patch('my_app.services.training_recommendations.timezone.now')
    def test_recent_activity_window_uses_requested_timezone(self, mocked_now):
        mocked_now.return_value = datetime(2026, 10, 15, 20, 0, tzinfo=ZoneInfo('UTC'))
        activity = PracticeActivity.objects.create(user=self.user, name='Evening session')
        PracticeCompletion.objects.create(
            user=self.user, activity=activity, activity_name=activity.name,
            practice_date=date(2026, 10, 16),
        )
        payload = {
            'sport_id': self.sport.pk, 'goal': 'general_fitness',
            'duration_minutes': 30,
        }
        utc = self.client.post('/api/v1/training/recommendations/',
                               {**payload, 'timezone': 'UTC'}, format='json')
        india = self.client.post('/api/v1/training/recommendations/',
                                 {**payload, 'timezone': 'Asia/Kolkata'}, format='json')
        self.assertEqual(utc.status_code, 200, utc.data)
        self.assertEqual(india.status_code, 200, india.data)
        self.assertEqual(utc.data['data']['generated_for']['week_of'], '2026-10-15')
        self.assertEqual(india.data['data']['generated_for']['week_of'], '2026-10-16')
        self.assertEqual(utc.data['data']['recent_context']['checklist_completions_last_7_days'], 0)
        self.assertEqual(india.data['data']['recent_context']['checklist_completions_last_7_days'], 1)
        invalid = self.client.post('/api/v1/training/recommendations/',
                                   {**payload, 'timezone': 'Not_A_Zone'}, format='json')
        self.assertEqual(invalid.status_code, 400)


class DailyPracticeApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='practice-user', email='practice@example.com', password='pw123456')
        self.other = User.objects.create_user(username='practice-other', email='other-practice@example.com', password='pw123456')
        self.client.force_authenticate(self.user)

    def test_get_does_not_mark_activities_and_completion_persists_once(self):
        response = self.client.get('/api/v1/practice/daily/?timezone=Asia%2FKolkata')
        self.assertEqual(response.status_code, 200)
        items = response.data['data']['activities']
        self.assertEqual(len(items), 5)
        self.assertEqual(response.data['data']['completed_count'], 0)
        self.assertEqual(PracticeCompletion.objects.count(), 0)
        activity_id = items[0]['id']
        completion_url = f'/api/v1/practice/daily/{activity_id}/completion/'
        payload = {'date': timezone.localtime(timezone.now(), timezone.get_fixed_timezone(330)).date().isoformat(), 'timezone': 'Asia/Kolkata', 'completed': True}
        first_response = self.client.patch(completion_url, payload, format='json')
        self.assertEqual(first_response.status_code, 200, first_response.content.decode()[:300])
        self.client.patch(completion_url, payload, format='json')
        self.assertEqual(PracticeCompletion.objects.filter(user=self.user).count(), 1)

    def test_defaults_are_seeded_even_after_a_custom_activity_and_duplicates_are_validation_errors(self):
        custom = self.client.post('/api/v1/practice/activities/', {
            'name': 'Serve practice', 'description': 'Practice placement',
        }, format='json')
        self.assertEqual(custom.status_code, 201, custom.data)
        checklist = self.client.get('/api/v1/practice/daily/')
        self.assertEqual(checklist.status_code, 200, checklist.data)
        names = {row['name'] for row in checklist.data['data']['activities']}
        self.assertTrue({'Warm-up', 'Skill practice', 'Stretching'}.issubset(names))
        duplicate = self.client.post('/api/v1/practice/activities/', {
            'name': 'Serve practice',
        }, format='json')
        self.assertEqual(duplicate.status_code, 400)

    def test_activity_edit_keeps_previous_day_snapshot_and_other_user_is_private(self):
        day = timezone.now().date() - timedelta(days=1)
        activity = PracticeActivity.objects.create(user=self.user, name='Footwork', sort_order=10)
        PracticeCompletion.objects.create(user=self.user, activity=activity, activity_name='Footwork', practice_date=day)
        own = self.client.patch(f'/api/v1/practice/activities/{activity.public_id}/', {'name': 'Agility drills'}, format='json')
        self.assertEqual(own.status_code, 200)
        history = self.client.get(f'/api/v1/practice/history/?start={day.isoformat()}&end={day.isoformat()}&timezone=UTC')
        self.assertEqual(history.data['data']['days'][0]['activities'][0]['name'], 'Footwork')
        other_activity = PracticeActivity.objects.create(user=self.other, name='Private practice')
        denied = self.client.patch(f'/api/v1/practice/daily/{other_activity.public_id}/completion/', {'date': day.isoformat(), 'timezone': 'UTC', 'completed': True}, format='json')
        self.assertEqual(denied.status_code, 404)

    def test_timezone_date_boundary_and_progress_aggregate(self):
        today_local = timezone.localtime(timezone.now(), timezone.get_fixed_timezone(330)).date()
        activities = self.client.get('/api/v1/practice/activities/').data['data']
        for activity in activities[:2]:
            self.client.patch(f"/api/v1/practice/daily/{activity['public_id']}/completion/", {'date': today_local.isoformat(), 'timezone': 'Asia/Kolkata', 'completed': True}, format='json')
        day = self.client.get(f'/api/v1/practice/daily/?date={today_local.isoformat()}&timezone=Asia%2FKolkata')
        self.assertEqual(day.data['data']['total_count'], 5)
        self.assertEqual(day.data['data']['completed_count'], 2)
        self.assertEqual(day.data['data']['completion_percentage'], 40)
        future = self.client.get(f'/api/v1/practice/daily/?date={(today_local + timedelta(days=1)).isoformat()}&timezone=Asia%2FKolkata')
        self.assertEqual(future.status_code, 400)

    @patch('my_app.services.practice.timezone.now')
    def test_omitted_practice_and_history_dates_default_in_requested_timezone(self, mocked_now):
        mocked_now.return_value = datetime(2026, 10, 15, 20, 0, tzinfo=ZoneInfo('UTC'))
        day = self.client.get('/api/v1/practice/daily/?timezone=Asia%2FKolkata')
        self.assertEqual(day.status_code, 200, day.data)
        self.assertEqual(day.data['data']['date'], '2026-10-16')
        history = self.client.get('/api/v1/practice/history/?timezone=Asia%2FKolkata')
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(history.data['data']['days'][0]['date'], '2026-10-16')
        invalid = self.client.get('/api/v1/practice/daily/?timezone=Not_A_Zone')
        self.assertEqual(invalid.status_code, 400)


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
        self.venue = Venue.objects.create(
            source_ground=self.ground, name='Central Approved Venue',
            address='1 Central Road', city='Bengaluru', latitude=12.9716,
            longitude=77.5946, opening_time='06:00', closing_time='23:00',
            active=True,
        )
        VenueSport.objects.create(venue=self.venue, sport=self.sport)
        self.organizer = User.objects.create_user(username='organizer', email='organizer@example.com', password='pw123456')
        self.player = User.objects.create_user(username='player', email='player@example.com', password='pw123456')
        PlayerProfile.objects.create(user=self.organizer)
        PlayerProfile.objects.create(user=self.player)
        self.organizer.latitude = self.player.latitude = 12.9716
        self.organizer.longitude = self.player.longitude = 77.5946
        self.organizer.save()
        self.player.save()

    def _create_ai_game(self, *, sport, reference):
        game_date = timezone.now().date() + timedelta(days=2)
        starts_at = timezone.make_aware(
            datetime.combine(game_date, time(10, 0)), timezone.get_current_timezone(),
        )
        ends_at = starts_at + timedelta(hours=1)
        venue = Venue.objects.create(
            name=f'{reference} venue', address='1 Game Road', city='Bengaluru',
            latitude=12.9716, longitude=77.5946, opening_time='08:00',
            closing_time='22:00', timezone='Asia/Kolkata',
        )
        court = Court.objects.create(
            venue=venue, sport=sport, name=f'{reference} court', capacity=6,
            price_per_hour=500,
        )
        game = Game.objects.create(
            game_reference=reference, host=self.organizer, sport=sport,
            venue=venue, court=court, game_date=game_date,
            start_time=starts_at.timetz().replace(tzinfo=None),
            end_time=ends_at.timetz().replace(tzinfo=None),
            starts_at=starts_at, ends_at=ends_at, min_players=2, max_players=6,
            skill_level='intermediate', status=Game.Status.OPEN,
        )
        GamePlayer.objects.create(game=game, user=self.organizer)
        return game

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
        # The organizer is registered atomically as participant one during
        # creation; the second user fills the next slot.
        match = Match.objects.get(pk=match_id)
        self.assertEqual(match.joined_count(), 2)
        self.assertTrue(match.joined_players.filter(pk=self.organizer.pk).exists())
        self.assertTrue(match.joined_players.filter(pk=self.player.pk).exists())
        self.assertEqual(self.organizer.notifications.count(), 1)
        notification=self.organizer.notifications.get()
        self.assertEqual(notification.data['match_id'],str(Match.objects.get(pk=match_id).public_id))
        self.assertTrue(notification.event_key)

    def test_match_notification_public_id_resolves_to_the_correct_match(self):
        match=Match.objects.create(sport=self.sport,ground=self.ground,organizer=self.organizer,date_time=timezone.now()+timedelta(days=1),total_players=3)
        self.client.force_authenticate(self.player)
        response=self.client.get(f'/api/v1/matches/public/{match.public_id}/')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['match']['public_id'],str(match.public_id))

    def test_match_detail_reads_current_roster_and_requester_state(self):
        match = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(days=1), total_players=3,
        )
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)

        response = self.client.get(f'/api/v1/matches/{match.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['match']['id'], match.pk)
        self.assertTrue(response.data['match']['is_joined'])
        self.assertFalse(response.data['match']['is_organizer'])
        self.assertEqual(response.data['match']['joinedPlayers'], 1)
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(f'/api/v1/matches/{match.pk}/').status_code, 401)

    def test_rejoined_player_can_be_removed_again_and_gets_a_new_notification(self):
        match = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(days=1), total_players=2,
        )
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.organizer)

        first = self.client.post(
            f'/api/v1/matches/{match.pk}/participants/{self.player.pk}/remove/',
        )
        self.assertEqual(first.status_code, 200)
        self.client.force_authenticate(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{match.pk}/join/').status_code, 200)
        self.client.force_authenticate(self.organizer)
        second = self.client.post(
            f'/api/v1/matches/{match.pk}/participants/{self.player.pk}/remove/',
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.player.notifications.filter(title='Removed from match').count(), 2)
        self.assertEqual(
            MatchParticipation.objects.filter(
                match=match, user=self.player, status=MatchParticipation.Status.REMOVED,
            ).count(),
            2,
        )

    def test_expired_match_cannot_be_joined(self):
        match = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() - timedelta(minutes=1), total_players=2,
        )
        self.client.force_authenticate(self.player)

        response = self.client.post(f'/api/v1/matches/{match.id}/join/')

        self.assertEqual(response.status_code, 409)
        self.assertIn('expired', response.data['error']['message'].lower())
        self.assertEqual(match.joined_count(), 0)

    def test_cannot_create_match_in_the_past(self):
        self.client.force_authenticate(self.organizer)
        response = self.client.post('/api/v1/matches/create/', {
            'sport': self.sport.id,
            'ground': self.ground.id,
            'date_time': (timezone.now() - timedelta(minutes=1)).isoformat(),
            'total_players': 2,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_match_end_time_must_follow_start_time(self):
        self.client.force_authenticate(self.organizer)
        starts = timezone.now() + timedelta(days=1)
        response = self.client.post('/api/v1/matches/create/', {
            'sport': self.sport.id, 'ground': self.ground.id,
            'date_time': starts.isoformat(), 'end_date_time': starts.isoformat(), 'total_players': 2,
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_player_can_leave_upcoming_match_and_reopen_slot(self):
        match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(days=1), total_players=1)
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)
        response = self.client.post(f'/api/v1/matches/{match.id}/leave/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Match.objects.get(pk=match.pk).joined_count(), 0)
        again = self.client.post(f'/api/v1/matches/{match.id}/leave/')
        self.assertEqual(again.status_code, 409)
        history = MatchParticipation.objects.get(match=match, user=self.player)
        self.assertEqual(history.status, MatchParticipation.Status.LEFT)
        self.assertIsNotNone(history.ended_at)
        self.assertEqual(self.organizer.notifications.filter(title='Player left your match').count(), 1)

    def test_organizer_cancellation_notifies_players_and_blocks_later_actions(self):
        match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(days=1), total_players=2)
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)
        denied = self.client.post(f'/api/v1/matches/{match.id}/cancel/')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.organizer)
        response = self.client.post(f'/api/v1/matches/{match.id}/cancel/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['match']['status'], 'cancelled')
        self.assertEqual(self.player.notifications.filter(title='Match cancelled').count(), 1)
        self.client.force_authenticate(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{match.id}/leave/').status_code, 409)

    def test_full_completed_and_cancelled_matches_reject_actions_without_losing_roster(self):
        full = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(days=1), total_players=1,
        )
        full.joined_players.add(self.organizer)
        self.client.force_authenticate(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{full.id}/join/').status_code, 409)

        completed = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() - timedelta(days=1), total_players=2, status='completed',
        )
        completed.joined_players.add(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{completed.id}/leave/').status_code, 409)
        self.assertTrue(completed.joined_players.filter(pk=self.player.pk).exists())

        cancelled = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(days=1), total_players=2, status='cancelled',
        )
        cancelled.joined_players.add(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{cancelled.id}/leave/').status_code, 409)
        self.assertTrue(cancelled.joined_players.filter(pk=self.player.pk).exists())

    def test_removal_records_reason_and_rejects_nonorganizer(self):
        match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(days=1), total_players=2)
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{match.id}/participants/{self.organizer.id}/remove/').status_code, 403)
        self.client.force_authenticate(self.organizer)
        response = self.client.post(f'/api/v1/matches/{match.id}/participants/{self.player.id}/remove/', {'reason': 'Roster update'}, format='json')
        self.assertEqual(response.status_code, 200)
        audit = MatchParticipation.objects.get(match=match, user=self.player)
        self.assertEqual((audit.status, audit.reason), (MatchParticipation.Status.REMOVED, 'Roster update'))
        self.assertEqual(match.joined_count(), 0)
        self.assertEqual(self.player.notifications.filter(title='Removed from match').count(), 1)

    def test_only_organizer_can_edit_and_capacity_cannot_drop_below_roster(self):
        match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(days=1), total_players=2)
        match.joined_players.add(self.player)
        second = User.objects.create_user(username='second-match-player', email='second-match-player@example.com', password='pw123456')
        match.joined_players.add(second)
        self.client.force_authenticate(self.player)
        denied = self.client.patch(f'/api/v1/matches/{match.id}/', {'total_players': 3}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.organizer)
        invalid = self.client.patch(f'/api/v1/matches/{match.id}/', {'total_players': 1}, format='json')
        self.assertEqual(invalid.status_code, 400)
        expanded = self.client.patch(f'/api/v1/matches/{match.id}/', {'total_players': 3}, format='json')
        self.assertEqual(expanded.status_code, 200)
        self.assertEqual(expanded.data['match']['total_players'], 3)

    @patch('my_project.settings.MATCH_RESCUE_WINDOW_MINUTES', 360)
    def test_withdrawal_notifies_limited_eligible_candidates_and_accepts_explicitly(self):
        candidate = User.objects.create_user(username='candidate', email='candidate@example.com', password='pw123456', latitude=12.9716, longitude=77.5946)
        PlayerProfile.objects.create(user=candidate)
        UserSport.objects.create(user=candidate, sport=self.sport, skill_level='intermediate')
        match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(hours=2), total_players=2)
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)
        left = self.client.post(f'/api/v1/matches/{match.id}/leave/')
        self.assertEqual(left.status_code, 200)
        invitation = MatchReplacementInvitation.objects.get(candidate=candidate)
        self.assertEqual(candidate.notifications.count(), 1)
        self.assertNotIn('username', invitation.opportunity.match.organizer.username)
        self.assertEqual(match.joined_count(), 0)
        self.client.force_authenticate(candidate)
        response = self.client.post(f'/api/v1/match-rescue-invitations/{invitation.public_id}/accept/')
        self.assertEqual(response.status_code, 200)
        match.refresh_from_db()
        self.assertTrue(match.joined_players.filter(pk=candidate.pk).exists())
        self.assertEqual(match.joined_count(), 1)
        self.assertEqual(
            MatchParticipation.objects.filter(
                match=match, user=candidate, status=MatchParticipation.Status.JOINED,
            ).count(),
            1,
        )
        self.assertTrue(MatchReplacementHistory.objects.filter(match=match, replacement_user=candidate).exists())
        self.assertEqual(MatchReplacementOpportunity.objects.get(pk=invitation.opportunity_id).closed_at is None, False)

    @patch('my_project.settings.MATCH_RESCUE_WINDOW_MINUTES', 360)
    def test_withdrawer_is_never_invited_back_and_ordinary_cancellation_does_not_trigger_rescue(self):
        UserSport.objects.create(user=self.player, sport=self.sport, skill_level='intermediate')
        candidate = User.objects.create_user(
            username='rescue-candidate', email='rescue-candidate@example.com', password='pw123456',
            latitude=12.9716, longitude=77.5946,
        )
        PlayerProfile.objects.create(user=candidate)
        UserSport.objects.create(user=candidate, sport=self.sport, skill_level='intermediate')
        match = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(hours=2), total_players=2,
        )
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{match.pk}/leave/').status_code, 200)
        self.assertFalse(MatchReplacementInvitation.objects.filter(candidate=self.player).exists())
        self.assertTrue(MatchReplacementInvitation.objects.filter(candidate=candidate).exists())

        ordinary = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(hours=8), total_players=2,
        )
        ordinary.joined_players.add(self.player)
        self.assertEqual(self.client.post(f'/api/v1/matches/{ordinary.pk}/leave/').status_code, 200)
        self.assertFalse(MatchReplacementOpportunity.objects.filter(match=ordinary).exists())

    def test_invitation_decline_persists_and_cannot_be_reused(self):
        match = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(hours=2), total_players=2,
        )
        opportunity = MatchReplacementOpportunity.objects.create(
            match=match, withdrawn_user=self.organizer,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        invitation = MatchReplacementInvitation.objects.create(
            opportunity=opportunity, candidate=self.player,
        )
        self.client.force_authenticate(self.player)
        declined = self.client.post(
            f'/api/v1/match-rescue-invitations/{invitation.public_id}/decline/',
        )
        self.assertEqual(declined.status_code, 200, declined.data)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, MatchReplacementInvitation.Status.DECLINED)
        self.assertIsNotNone(invitation.responded_at)
        rejected_accept = self.client.post(
            f'/api/v1/match-rescue-invitations/{invitation.public_id}/accept/',
        )
        self.assertEqual(rejected_accept.status_code, 409)

    def test_match_cancellation_closes_pending_rescue_invitations(self):
        match = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(hours=2), total_players=2,
        )
        opportunity = MatchReplacementOpportunity.objects.create(
            match=match, withdrawn_user=self.player,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        invitation = MatchReplacementInvitation.objects.create(
            opportunity=opportunity, candidate=self.player,
        )
        self.client.force_authenticate(self.organizer)
        response = self.client.post(f'/api/v1/matches/{match.pk}/cancel/')
        self.assertEqual(response.status_code, 200, response.data)
        opportunity.refresh_from_db()
        invitation.refresh_from_db()
        self.assertIsNotNone(opportunity.closed_at)
        self.assertEqual(invitation.status, MatchReplacementInvitation.Status.UNAVAILABLE)
        self.client.force_authenticate(self.player)
        self.assertEqual(self.client.post(
            f'/api/v1/match-rescue-invitations/{invitation.public_id}/accept/',
        ).status_code, 409)

    def test_only_one_invited_candidate_can_claim_the_last_slot(self):
        candidates = []
        for index in range(2):
            candidate = User.objects.create_user(
                username=f'claimant-{index}', email=f'claimant-{index}@example.com', password='pw123456',
                latitude=12.9716, longitude=77.5946,
            )
            PlayerProfile.objects.create(user=candidate)
            UserSport.objects.create(user=candidate, sport=self.sport, skill_level='intermediate')
            candidates.append(candidate)
        match = Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(hours=2), total_players=1,
        )
        opportunity = MatchReplacementOpportunity.objects.create(
            match=match, withdrawn_user=self.player,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        invitations = [MatchReplacementInvitation.objects.create(
            opportunity=opportunity, candidate=candidate,
        ) for candidate in candidates]
        self.client.force_authenticate(candidates[0])
        first = self.client.post(f'/api/v1/match-rescue-invitations/{invitations[0].public_id}/accept/')
        self.assertEqual(first.status_code, 200, first.data)
        self.client.force_authenticate(candidates[1])
        second = self.client.post(f'/api/v1/match-rescue-invitations/{invitations[1].public_id}/accept/')
        self.assertEqual(second.status_code, 409)
        match.refresh_from_db()
        self.assertEqual(match.joined_players.count(), 1)
        invitations[1].refresh_from_db()
        self.assertEqual(invitations[1].status, MatchReplacementInvitation.Status.UNAVAILABLE)

    @patch('my_project.settings.MATCH_RESCUE_WINDOW_MINUTES', 360)
    def test_rescue_excludes_blocked_players_and_expired_invitation_cannot_be_accepted(self):
        candidate = User.objects.create_user(username='candidate', email='candidate2@example.com', password='pw123456', latitude=12.9716, longitude=77.5946)
        PlayerProfile.objects.create(user=candidate)
        UserSport.objects.create(user=candidate, sport=self.sport)
        UserBlock.objects.create(user=self.organizer, blocked_user=candidate)
        match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(hours=2), total_players=2)
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)
        self.client.post(f'/api/v1/matches/{match.id}/leave/')
        self.assertFalse(MatchReplacementInvitation.objects.filter(candidate=candidate).exists())

        # An invitation may become stale after it was sent; acceptance rejects it.
        opportunity = MatchReplacementOpportunity.objects.create(match=match, withdrawn_user=self.player, expires_at=timezone.now()-timedelta(seconds=1))
        invitation = MatchReplacementInvitation.objects.create(opportunity=opportunity, candidate=candidate)
        self.client.force_authenticate(candidate)
        response = self.client.post(f'/api/v1/match-rescue-invitations/{invitation.public_id}/accept/')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Match.objects.get(pk=match.pk).joined_count(), 0)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, MatchReplacementInvitation.Status.EXPIRED)

    def test_invitation_cannot_be_accepted_by_another_user(self):
        opportunity = MatchReplacementOpportunity.objects.create(
            match=Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now()+timedelta(hours=2), total_players=2),
            expires_at=timezone.now()+timedelta(hours=1),
        )
        invitation = MatchReplacementInvitation.objects.create(opportunity=opportunity, candidate=self.player)
        self.client.force_authenticate(self.organizer)
        response = self.client.post(f'/api/v1/match-rescue-invitations/{invitation.public_id}/accept/')
        self.assertEqual(response.status_code, 403)

    def test_only_organizer_can_remove_participant_and_notification_is_idempotent(self):
        match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(days=1), total_players=2)
        match.joined_players.add(self.player)
        self.client.force_authenticate(self.player)
        denied = self.client.post(f'/api/v1/matches/{match.id}/participants/{self.organizer.id}/remove/')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.organizer)
        removed = self.client.post(f'/api/v1/matches/{match.id}/participants/{self.player.id}/remove/', {'reason': 'Roster changed'}, format='json')
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.player.notifications.filter(title='Removed from match').count(), 1)
        repeated = self.client.post(f'/api/v1/matches/{match.id}/participants/{self.player.id}/remove/')
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(self.player.notifications.filter(title='Removed from match').count(), 1)

    def test_validation_errors_use_standard_envelope(self):
        self.client.force_authenticate(self.organizer)
        response = self.client.post('/api/v1/matches/create/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])
        self.assertEqual(response.data['error']['code'], 'VALIDATION_ERROR')

    def test_ai_recommendations_return_paginated_matches(self):
        UserSport.objects.create(user=self.player, sport=self.sport, skill_level='intermediate', preferred=True)
        self._create_ai_game(sport=self.sport, reference='AI-FOOTBALL-1')
        self.client.force_authenticate(self.player)
        response = self.client.get('/api/v1/ai/matches/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['sport']['name'], self.sport.name)
        self.assertIn('match_score', response.data[0])

    def test_ai_recommendations_exclude_sports_not_in_player_profile(self):
        cricket = Sport.objects.get(name='Cricket')
        UserSport.objects.create(user=self.player, sport=self.sport, skill_level='intermediate', preferred=True)
        self._create_ai_game(sport=self.sport, reference='AI-FOOTBALL-2')
        self._create_ai_game(sport=cricket, reference='AI-CRICKET-1')
        self.client.force_authenticate(self.player)
        response = self.client.get('/api/v1/ai/matches/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['sport']['name'], self.sport.name)

    def test_ai_recommendations_exclude_full_and_cancelled_games(self):
        UserSport.objects.create(user=self.player, sport=self.sport, skill_level='intermediate', preferred=True)
        full = self._create_ai_game(sport=self.sport, reference='AI-FOOTBALL-3')
        full.status = Game.Status.FULL
        full.save(update_fields=['status', 'updated_at'])
        cancelled = self._create_ai_game(sport=self.sport, reference='AI-FOOTBALL-4')
        cancelled.status = Game.Status.CANCELLED
        cancelled.save(update_fields=['status', 'updated_at'])
        self.client.force_authenticate(self.player)
        response = self.client.get('/api/v1/ai/matches/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])


class AuthenticationApiTests(APITestCase):
    register_url = '/api/v1/auth/register/'
    venue_owner_register_url = '/api/v1/auth/register/venue-owner/'
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
        response = self.client.post(self.register_url, self.payload(city='Bengaluru'), format='json')
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='asha@example.com')
        self.assertTrue(user.check_password('StrongPass!123'))
        self.assertNotEqual(user.password, 'StrongPass!123')
        self.assertTrue(PlayerProfile.objects.filter(user=user).exists())
        self.assertEqual(user.city, 'Bengaluru')
        self.assertIn('access', response.data['tokens'])
        self.assertNotIn('password', response.data['user'])

    def test_venue_owner_registration_creates_pending_application_without_player_profile(self):
        response = self.client.post(self.venue_owner_register_url, {
            **self.payload(username='arena-owner', email='owner@example.com', city='Bengaluru'),
            'business_name': 'Arena Sports', 'address': '1 Sports Road',
        }, format='json')
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email='owner@example.com')
        self.assertFalse(PlayerProfile.objects.filter(user=user).exists())
        application = user.venue_owner_applications.get()
        self.assertEqual(application.status, 'pending')
        self.assertEqual(application.business_name, 'Arena Sports')

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

    def test_login_only_accepts_matching_player_venue_and_admin_audiences(self):
        player = self.create_user(username='audience-player', email='audience-player@example.com')
        venue_owner = User.objects.create_user(
            username='audience-venue', email='audience-venue@example.com', password='StrongPass!123'
        )
        venue_owner.groups.add(Group.objects.create(name='SportMate Venue Owners'))
        PlayerProfile.objects.create(user=venue_owner)
        admin = User.objects.create_user(
            username='audience-admin', email='audience-admin@example.com', password='StrongPass!123'
        )
        admin.groups.add(Group.objects.create(name='SportMate Admins'))

        def login(user, audience):
            cache.clear()
            return self.client.post(self.login_url, {
                'identifier': user.email,
                'password': 'StrongPass!123',
                'audience': audience,
            }, format='json')

        self.assertEqual(login(player, 'player').status_code, 200)
        self.assertEqual(login(player, 'venue').status_code, 403)
        self.assertEqual(login(player, 'admin').status_code, 403)
        self.assertEqual(login(venue_owner, 'venue').status_code, 200)
        self.assertEqual(login(venue_owner, 'player').status_code, 200)
        self.assertEqual(login(venue_owner, 'admin').status_code, 403)
        self.assertEqual(login(admin, 'admin').status_code, 200)
        self.assertEqual(login(admin, 'player').status_code, 403)
        self.assertEqual(login(admin, 'venue').status_code, 403)

    def test_django_model_permission_does_not_turn_player_into_admin(self):
        player = self.create_user(username='permission-player', email='permission-player@example.com')
        view_user = Permission.objects.get(codename='view_user', content_type__app_label='my_app')
        player.user_permissions.add(view_user)

        player_login = self.client.post(self.login_url, {
            'identifier': player.email, 'password': 'StrongPass!123', 'audience': 'player',
        }, format='json')
        admin_login = self.client.post(self.login_url, {
            'identifier': player.email, 'password': 'StrongPass!123', 'audience': 'admin',
        }, format='json')

        self.assertEqual(player_login.status_code, 200)
        self.assertEqual(admin_login.status_code, 403)
        self.client.force_authenticate(player)
        self.assertEqual(self.client.get('/api/v1/admin/dashboard/').status_code, 403)

    def test_role_management_prevents_admin_overlap_and_can_correct_misassigned_player(self):
        superowner = User.objects.create_user(
            username='role-superowner', email='role-superowner@example.com', password='StrongPass!123'
        )
        superowner.groups.add(Group.objects.create(name='SportMate Admins'))
        player = self.create_user(username='misassigned-player', email='misassigned-player@example.com')
        venue_owner = User.objects.create_user(
            username='role-venue-owner', email='role-venue-owner@example.com', password='StrongPass!123'
        )
        venue_owner.groups.add(Group.objects.create(name='SportMate Venue Owners'))
        admin_url = f'/api/v1/admin/access/users/{player.public_id}/'
        venue_url = f'/api/v1/admin/access/users/{venue_owner.public_id}/'

        self.client.force_authenticate(superowner)
        denied_player_promotion = self.client.patch(admin_url, {
            'role': 'SportMate Admins', 'action': 'grant', 'reason': 'Must remain a player.'
        }, format='json')
        denied_venue_promotion = self.client.patch(venue_url, {
            'role': 'SportMate Admins', 'action': 'grant', 'reason': 'Must remain venue owner.'
        }, format='json')
        self.assertEqual(denied_player_promotion.status_code, 400)
        self.assertEqual(denied_venue_promotion.status_code, 400)

        # A previously misassigned explicit role can be removed by the superowner.
        player.groups.add(Group.objects.get(name='SportMate Admins'))
        revoked = self.client.patch(admin_url, {
            'role': 'SportMate Admins', 'action': 'revoke', 'reason': 'Correct player role.'
        }, format='json')
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.assertEqual(self.client.post(self.login_url, {
            'identifier': player.email, 'password': 'StrongPass!123', 'audience': 'player',
        }, format='json').status_code, 200)

        admin_target = User.objects.create_user(
            username='role-admin-target', email='role-admin-target@example.com', password='StrongPass!123'
        )
        admin_target.groups.add(Group.objects.get(name='SportMate Admins'))
        venue_grant_to_admin = self.client.patch(
            f'/api/v1/admin/access/users/{admin_target.public_id}/',
            {'role': 'SportMate Venue Owners', 'action': 'grant', 'reason': 'Must remain admin.'},
            format='json',
        )
        self.assertEqual(venue_grant_to_admin.status_code, 400)

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


class ReferralApiTests(APITestCase):
    def setUp(self):
        self.referrer = User.objects.create_user(
            username='referring-player', email='referring@example.com', password='StrongPass!123',
        )
        PlayerProfile.objects.create(user=self.referrer)
        self.client.force_authenticate(self.referrer)

    def signup_payload(self, **changes):
        data = {
            'name': 'Invited Player', 'username': 'invited-player', 'email': 'invited@example.com',
            'phone': '', 'city': 'Bengaluru', 'password': 'StrongPass!123',
        }
        data.update(changes)
        return data

    def test_each_player_gets_unique_private_referral_code(self):
        first = self.client.get('/api/v1/users/me/referrals/')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.data['data']['referral_code']), 16)
        self.client.force_authenticate(None)
        second_user = User.objects.create_user(username='second-referrer', email='second@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=second_user)
        self.client.force_authenticate(second_user)
        second = self.client.get('/api/v1/users/me/referrals/')
        self.assertNotEqual(first.data['data']['referral_code'], second.data['data']['referral_code'])

    def test_valid_signup_is_attributed_once_and_notifies_without_identity(self):
        code = self.client.get('/api/v1/users/me/referrals/').data['data']['referral_code']
        payload = self.signup_payload(referral_code=code.lower())
        created = self.client.post('/api/v1/auth/register/', payload, format='json')
        self.assertEqual(created.status_code, 201)
        referred = User.objects.get(email='invited@example.com')
        self.assertTrue(referred.referral_code)
        attribution = referred.referral_attribution
        self.assertEqual(attribution.referrer, self.referrer)
        self.assertEqual(attribution.status, 'successful')
        self.assertEqual(ReferralAttribution.objects.filter(referrer=self.referrer).count(), 1)
        self.assertTrue(self.referrer.notifications.filter(type='referral').exists())
        self.assertNotIn(referred.username, self.referrer.notifications.get(type='referral').body)

        self.client.force_authenticate(self.referrer)
        stats = self.client.get('/api/v1/users/me/referrals/').data['data']
        self.assertEqual(stats['total_referrals'], 1)
        self.assertEqual(stats['successful_registrations'], 1)
        self.assertNotIn('email', stats)
        self.assertNotIn('referred_users', stats)

        duplicate = self.client.post('/api/v1/auth/register/', self.signup_payload(
            username='invited-again', referral_code=code,
        ), format='json')
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(ReferralAttribution.objects.filter(referrer=self.referrer).count(), 1)

    def test_invalid_and_self_referral_codes_are_rejected_without_creating_accounts(self):
        invalid = self.client.post('/api/v1/auth/register/', self.signup_payload(referral_code='NOT-A-CODE'), format='json')
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(User.objects.filter(email='invited@example.com').exists())
        code = self.client.get('/api/v1/users/me/referrals/').data['data']['referral_code']
        with self.assertRaises(ValidationError):
            ReferralService.resolve_referrer(code, email=self.referrer.email)

    def test_venue_owner_cannot_get_or_use_player_referrals(self):
        venue_owner = User.objects.create_user(username='referral-venue', email='venue-referral@example.com', password='StrongPass!123')
        self.client.force_authenticate(venue_owner)
        self.assertEqual(self.client.get('/api/v1/users/me/referrals/').status_code, 403)


class PlayerChallengeAndAchievementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='challenge-player', email='challenge@example.com', password='StrongPass!123',
        )
        PlayerProfile.objects.create(user=self.user)
        self.sport = Sport.objects.get(name='Badminton')
        UserSport.objects.create(user=self.user, sport=self.sport, skill_level=UserSport.SkillLevel.INTERMEDIATE)
        self.today = timezone.localdate()
        self.client.force_authenticate(self.user)

    def make_challenge(self, **changes):
        values = {
            'name': 'Practice streak', 'description': 'Complete a practice session.',
            'sport': self.sport, 'kind': SportChallenge.Kind.INDIVIDUAL,
            'metric': SportChallenge.Metric.CHECKLIST_COMPLETIONS, 'target': 1,
            'starts_on': self.today, 'ends_on': self.today + timedelta(days=4),
            'reward_points': 25, 'created_by': self.user,
        }
        values.update(changes)
        return SportChallenge.objects.create(**values)

    def test_challenge_join_progress_and_reward_are_server_calculated_and_idempotent(self):
        achievement = Achievement.objects.create(
            code='practice-challenge-reward', name='Practice finisher',
            description='Complete a challenge.', category=Achievement.Category.ACTIVITY,
            target_value=1,
        )
        challenge = self.make_challenge(reward_achievement=achievement)
        joined = self.client.post(f'/api/v1/challenges/{challenge.public_id}/join/', {}, format='json')
        self.assertEqual(joined.status_code, 201)
        self.assertEqual(joined.data['data']['progress'], 0)
        self.assertFalse(joined.data['data']['completed'])

        activity = PracticeActivity.objects.create(user=self.user, name='Warm-up')
        PracticeCompletion.objects.create(
            user=self.user, activity=activity, activity_name='Warm-up', practice_date=self.today,
        )
        first = self.client.get('/api/v1/challenges/').data['data'][0]
        second = self.client.get('/api/v1/challenges/').data['data'][0]
        self.assertEqual(first['my_entry']['progress'], 1)
        self.assertTrue(first['my_entry']['completed'])
        self.assertEqual(first['my_entry']['reward_points'], 25)
        self.assertEqual(second['my_entry']['reward_points'], 25)
        self.assertEqual(ChallengeRewardGrant.objects.filter(entry__challenge=challenge, user=self.user).count(), 1)
        self.assertTrue(UserAchievement.objects.get(user=self.user, achievement=achievement).unlocked)

    def test_duplicate_join_and_expired_challenge_are_rejected(self):
        challenge = self.make_challenge()
        endpoint = f'/api/v1/challenges/{challenge.public_id}/join/'
        self.assertEqual(self.client.post(endpoint, {}, format='json').status_code, 201)
        self.assertEqual(self.client.post(endpoint, {}, format='json').status_code, 400)
        expired = self.make_challenge(name='Expired challenge', starts_on=self.today - timedelta(days=3), ends_on=self.today - timedelta(days=1))
        response = self.client.post(f'/api/v1/challenges/{expired.public_id}/join/', {}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_only_entry_owner_can_leave_and_withdrawal_cannot_be_repeated(self):
        challenge = self.make_challenge()
        joined = self.client.post(f'/api/v1/challenges/{challenge.public_id}/join/', {}, format='json')
        entry_id = joined.data['data']['public_id']
        other = User.objects.create_user(username='other-challenge', email='other-challenge@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=other)
        self.client.force_authenticate(other)
        self.assertEqual(self.client.post(f'/api/v1/challenge-entries/{entry_id}/leave/').status_code, 403)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post(f'/api/v1/challenge-entries/{entry_id}/leave/').status_code, 200)
        self.assertEqual(self.client.post(f'/api/v1/challenge-entries/{entry_id}/leave/').status_code, 400)

    def test_challenge_filters_validate_and_player_privacy_is_enforced(self):
        self.make_challenge()
        self.assertEqual(self.client.get('/api/v1/challenges/?kind=invalid').status_code, 400)
        other = User.objects.create_user(username='private-challenge-player', email='private-challenge@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=other, profile_visibility='private')
        self.assertEqual(self.client.get(f'/api/v1/players/{other.public_id}/achievements/').status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/v1/challenges/').status_code, 401)

    def test_achievement_evaluation_handles_unsupported_game_wins_and_keeps_unlock(self):
        achievement = Achievement.objects.create(
            code='unsupported-wins', name='Match winner', description='Win matches.',
            category=Achievement.Category.WINS, target_value=1,
        )
        rows = AchievementEvaluationService.evaluate(user=self.user)
        row = next(item for item in rows if item.achievement_id == achievement.id)
        self.assertEqual(row.progress, 0)
        row.unlocked = True
        row.progress = 1
        row.unlocked_at = timezone.now()
        row.save()
        AchievementEvaluationService.evaluate(user=self.user)
        row.refresh_from_db()
        self.assertTrue(row.unlocked)
        self.assertGreaterEqual(row.progress, achievement.target_value)


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

    def test_sport_skill_is_player_selected_and_completed_matches_are_counted(self):
        UserSport.objects.create(
            user=self.user, sport=self.sport,
            skill_level=UserSport.SkillLevel.INTERMEDIATE,
        )
        ground = Ground.objects.create(
            name='Profile statistics ground', city='Bengaluru',
            latitude=12.97, longitude=77.59,
        )
        match = Match.objects.create(
            organizer=self.user, sport=self.sport, ground=ground,
            date_time=timezone.now() - timedelta(hours=2),
            end_date_time=timezone.now() - timedelta(hours=1),
            total_players=2, status='completed',
        )
        match.joined_players.add(self.user)

        response = self.client.get('/api/v1/users/me/sports/')

        self.assertEqual(response.status_code, 200)
        item = response.data['data'][0]
        self.assertEqual(item['skill_level'], 'intermediate')
        self.assertEqual(item['matches_played'], 1)
        self.assertEqual(item['wins'], 0)

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

    def test_public_player_detail_uses_public_id_and_includes_public_avatar_field(self):
        response = self.client.get(f'/api/v1/players/public/{self.other.public_id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['public_id'], str(self.other.public_id))
        self.assertIn('avatar', response.data)
        self.assertNotIn('email', response.data)
        self.assertNotIn('phone', response.data)
        self.assertNotIn('latitude', response.data)
        self.assertNotIn('longitude', response.data)

    def test_matching_mood_preference_is_changeable_and_validated(self):
        accepted = self.client.patch('/api/v1/users/me/player-profile/', {'matching_mood': 'social'}, format='json')
        rejected = self.client.patch('/api/v1/users/me/player-profile/', {'matching_mood': 'competitive'}, format='json')
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.data['player_profile']['matching_mood'], 'social')
        self.assertEqual(rejected.status_code, 400)

    def test_private_profiles_are_not_public(self):
        self.other.profile.profile_visibility = 'private'
        self.other.profile.save()
        response = self.client.get(f'/api/v1/players/{self.other.id}/')
        public_id_response = self.client.get(f'/api/v1/players/public/{self.other.public_id}/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(public_id_response.status_code, 404)

    def test_global_player_search_excludes_private_and_blocked_profiles(self):
        private = User.objects.create_user(
            username='hidden-player', email='hidden-player@example.com',
            password='StrongPass!123', full_name='Hidden Player', city='Bengaluru',
        )
        PlayerProfile.objects.create(user=private, profile_visibility='private')
        public_result = self.client.get('/api/v1/search/', {
            'type': 'player', 'q': 'Public Player',
        })
        private_result = self.client.get('/api/v1/search/', {
            'type': 'player', 'q': 'Hidden Player',
        })
        self.assertEqual(public_result.status_code, 200)
        self.assertEqual([row['title'] for row in public_result.data['results']], ['Public Player'])
        self.assertEqual(private_result.data['results'], [])

        UserBlock.objects.create(user=self.user, blocked_user=self.other)
        blocked_result = self.client.get('/api/v1/search/', {
            'type': 'player', 'q': 'Public Player',
        })
        self.assertEqual(blocked_result.data['results'], [])


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

    def test_home_player_feed_uses_database_distance_filter_and_bounded_results(self):
        response = self.client.get('/api/v1/players/', {
            'latitude': 12.9716, 'longitude': 77.5946, 'radius': 2, 'sport': 'badminton',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['username'] for row in response.data['results']], ['near-player'])
        self.assertLess(response.data['results'][0]['distance_km'], 1)
        self.assertEqual(response.data['results'][0]['rating_summary']['rating_count'], 0)
        legacy = self.client.get('/api/v1/players/', {
            'lat': 12.9716, 'lng': 77.5946, 'radius': 2, 'sport': 'badminton',
        })
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual([row['username'] for row in legacy.data['results']], ['near-player'])

    def test_player_directory_search_filters_and_sorting(self):
        response = self.client.get('/api/v1/players/', {
            'q': 'Near', 'city': 'Bengal', 'sport': 'BADMINTON',
            'skill_level': 'advanced', 'availability': 'available',
            'sort': 'name', 'page_size': 1,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['username'], 'near-player')

        PlayerRating.objects.create(
            reviewer=self.requester, player=self.near, rating=3.0,
            playing_performance=3, teamwork=3, sportsmanship=3, punctuality=3,
        )
        PlayerRating.objects.create(
            reviewer=self.requester, player=self.far, rating=4.5,
            playing_performance=5, teamwork=4, sportsmanship=5, punctuality=4,
        )
        top_rated = self.client.get('/api/v1/players/', {
            'q': 'Player', 'availability': 'all', 'sort': 'rating',
        })
        self.assertEqual(top_rated.data['results'][0]['username'], 'far-player')

    def test_player_directory_availability_and_private_blocked_exclusions(self):
        unavailable = User.objects.create_user(
            username='unavailable-public', email='unavailable@example.com',
            password='StrongPass!123', full_name='Unavailable Player',
            city='Bengaluru', is_available=False,
        )
        PlayerProfile.objects.create(user=unavailable, profile_visibility='public')
        private = User.objects.create_user(
            username='private-directory', email='private-directory@example.com',
            password='StrongPass!123', full_name='Private Player', city='Bengaluru',
        )
        PlayerProfile.objects.create(user=private, profile_visibility='private')
        UserBlock.objects.create(user=self.requester, blocked_user=self.near)

        result = self.client.get('/api/v1/players/', {
            'availability': 'unavailable', 'city': 'Bengaluru', 'sort': 'name',
        })
        usernames = [row['username'] for row in result.data['results']]
        self.assertEqual(usernames, ['unavailable-public'])

        # Blocks are enforced in either direction, including direct profile reads.
        self.assertEqual(
            self.client.get(f'/api/v1/players/public/{self.near.public_id}/').status_code,
            404,
        )
        UserBlock.objects.create(user=self.far, blocked_user=self.requester)
        blocked_by_result = self.client.get('/api/v1/players/', {'q': 'Far Player'})
        self.assertEqual(blocked_by_result.data['results'], [])

    def test_player_directory_paginates_incrementally_without_duplicates(self):
        for index in range(23):
            player = User.objects.create_user(
                username=f'directory-{index:02d}',
                email=f'directory-{index:02d}@example.com',
                password='StrongPass!123', full_name=f'Directory Player {index:02d}',
                city='Bengaluru',
            )
            PlayerProfile.objects.create(user=player, profile_visibility='public')

        first = self.client.get('/api/v1/players/', {
            'q': 'Directory Player', 'page': 1, 'page_size': 10, 'sort': 'name',
        })
        second = self.client.get('/api/v1/players/', {
            'q': 'Directory Player', 'page': 2, 'page_size': 10, 'sort': 'name',
        })
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['count'], 23)
        self.assertEqual(len(first.data['results']), 10)
        self.assertEqual(len(second.data['results']), 10)
        first_ids = {row['public_id'] for row in first.data['results']}
        second_ids = {row['public_id'] for row in second.data['results']}
        self.assertFalse(first_ids & second_ids)
        self.assertIsNotNone(first.data['next'])

    def test_player_directory_rejects_malformed_filters_and_requires_auth(self):
        for query in (
            {'sport': 'not-a-real-sport'},
            {'skill_level': 'legend'},
            {'availability': 'maybe'},
            {'sort': 'random'},
            {'page_size': 101},
            {'page': 0},
            {'page': 'not-a-number'},
            {'q': 'x' * 101},
            {'latitude': 12.9},
            {'radius': 0},
        ):
            response = self.client.get('/api/v1/players/', query)
            self.assertEqual(response.status_code, 400, (query, response.data))
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get('/api/v1/players/').status_code, 401)

    def test_nearby_venues_and_invalid_or_missing_coordinates(self):
        venues = self.client.get('/api/v1/grounds/', {
            'latitude': 12.9716, 'longitude': 77.5946, 'radius': 2, 'sport': 'badminton',
        })
        invalid = self.client.get('/api/v1/players/nearby/', {'latitude': 200, 'longitude': 77})
        missing = self.client.get('/api/v1/venues/nearby/', {'latitude': 12.9})
        self.assertEqual(venues.status_code, 200)
        self.assertEqual(venues.data['results'][0]['name'], 'Near Venue')
        self.assertEqual(venues.data['results'][0]['latitude'], 12.9721)
        self.assertEqual(venues.data['results'][0]['longitude'], 77.5949)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(missing.status_code, 400)


class GroundIngestionAndAutoGameTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='auto-game-user', email='auto-game@example.com',
            password='StrongPass!123', latitude=12.9716, longitude=77.5946,
        )
        PlayerProfile.objects.create(user=self.user, preferred_play_times=['evening'])
        self.sport = Sport.objects.get(name='Badminton')
        UserSport.objects.create(user=self.user, sport=self.sport, skill_level='intermediate', preferred=True)
        self.ground = Ground.objects.create(
            name='Verified Discovery Ground', city='Bengaluru', latitude=12.972,
            longitude=77.595, maps_place_id='openstreetmap_live:node/verified', source='openstreetmap_live',
        )
        self.venue = Venue.objects.create(
            source_ground=self.ground, name='Verified Discovery Venue', address='1 Sports Road',
            city='Bengaluru', latitude=12.972, longitude=77.595,
            opening_time='06:00', closing_time='23:00', active=True,
        )
        self.court = Court.objects.create(
            venue=self.venue, sport=self.sport, name='Court 1', capacity=4,
            price_per_hour=500, active=True,
        )
        self.client.force_authenticate(self.user)

    @patch('my_app.views.find_live_sports_grounds')
    def test_live_ground_search_supports_keyword_filters_and_external_booking_status(self, provider):
        provider.return_value = [{
            'id': 'places/real-place-id', 'name': 'Machaxi Sports Center',
            'address': 'Whitefield, Bengaluru', 'latitude': 12.972,
            'longitude': 77.595, 'types': ['sports_complex'],
            'mapsUrl': 'https://maps.google.com/?q=real-place-id',
            'websiteUrl': 'https://example.test', 'source': 'google_maps_live',
        }]
        response = self.client.get('/api/v1/grounds/live/', {
            'query': 'Machaxi Sports Center', 'sport': 'badminton',
            'category': 'sports centre', 'lat': 12.9716, 'lng': 77.5946,
            'radius': 5,
        })
        self.assertEqual(response.status_code, 200)
        provider.assert_called_once_with(
            latitude=12.9716, longitude=77.5946, radius_km=5.0,
            query='Machaxi Sports Center', sport='badminton', category='sports centre',
        )
        result = response.data['results'][0]
        self.assertEqual(result['name'], 'Machaxi Sports Center')
        self.assertFalse(result['booking_supported'])
        self.assertEqual(result['verification_status'], 'external_unverified')
        self.assertAlmostEqual(result['distance_km'], 0.06, places=1)

    def test_live_ground_search_rejects_invalid_or_partial_coordinates(self):
        invalid = self.client.get('/api/v1/grounds/live/', {
            'query': 'Badminton courts', 'lat': 91, 'lng': 77,
        })
        partial = self.client.get('/api/v1/grounds/live/', {
            'query': 'Badminton courts', 'lat': 12,
        })
        empty = self.client.get('/api/v1/grounds/live/')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(partial.status_code, 400)
        self.assertEqual(empty.status_code, 400)

    @patch('my_app.views.find_live_sports_grounds')
    def test_provider_places_are_not_written_to_ground_catalogue(self, provider):
        provider.return_value = [{
            'id': 'ChIJ-provider-only', 'name': 'Provider-only Turf',
            'address': 'Bengaluru', 'latitude': 12.973, 'longitude': 77.596,
            'types': ['sports_complex'], 'source': 'google_maps_live',
        }]
        response = self.client.post('/api/v1/grounds/discover/', {
            'latitude': 12.9717, 'longitude': 77.5947,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['created_count'], 0)
        self.assertEqual(response.data['data'], [])
        self.assertFalse(Ground.objects.filter(name='Provider-only Turf').exists())

    @patch('my_app.views.find_live_sports_grounds')
    def test_live_ground_search_drops_unnamed_or_incomplete_provider_results(self, provider):
        provider.return_value = [
            {'id': 'unnamed', 'name': '', 'latitude': 12.972, 'longitude': 77.595},
            {'id': 'no-location', 'name': 'A real facility'},
        ]
        response = self.client.get('/api/v1/grounds/live/', {
            'query': 'sports facilities',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'], [])

    @override_settings(GOOGLE_MAPS_PLACES_API_KEY='server-only-key')
    @patch('my_app.views.find_live_sports_grounds')
    @patch('my_app.views.UniversalGroundsView._refresh_from_overpass')
    def test_tournament_nearby_catalogue_keeps_numeric_osm_ids(self, overpass, google):
        osm_ground = SportsGround.objects.create(
            osm_id='node/osm-tournament-ground', name='Community Sports Ground',
            ground_type='pitch', sport='cricket', latitude=12.972,
            longitude=77.595,
        )
        SportsGround.objects.create(
            osm_id='google/legacy-cached-place', name='Legacy Google Ground',
            ground_type='sports_centre', sport='multi-sport', latitude=12.973,
            longitude=77.596,
        )
        response = self.client.get('/api/v1/grounds/nearby/', {
            'latitude': 12.9716, 'longitude': 77.5946,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([ground['name'] for ground in response.data['grounds']], ['Community Sports Ground'])
        self.assertEqual(response.data['grounds'][0]['id'], osm_ground.id)
        self.assertEqual(response.data['attribution'], '© OpenStreetMap contributors')
        google.assert_not_called()

    @patch('my_app.views.find_live_sports_grounds')
    def test_discovery_upserts_provider_grounds_and_saves_user_location(self, provider):
        provider.return_value = [{
            'id': 'node/123', 'name': 'Nearby Badminton Court', 'address': '2 Court Road, Bengaluru, Karnataka',
            'latitude': 12.973, 'longitude': 77.596, 'types': ['sports_centre', 'badminton'],
            'source': 'openstreetmap_live',
        }]
        payload = {'latitude': 12.9717, 'longitude': 77.5947, 'sport': 'badminton'}
        first = self.client.post('/api/v1/grounds/discover/', payload, format='json')
        second = self.client.post('/api/v1/grounds/discover/', payload, format='json')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data['created_count'], 1)
        self.assertEqual(second.data['created_count'], 0)
        self.assertEqual(Ground.objects.filter(maps_place_id='openstreetmap_live:node/123').count(), 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.latitude, 12.9717)

    def test_box_cricket_discovery_is_saved_as_a_small_cricket_ground(self):
        grounds, created = GroundIngestionService.ingest(places=[{
            'id': 'box-cricket-1', 'name': 'City Box Cricket Arena',
            'address': 'Sports Road, Bengaluru', 'latitude': 12.973,
            'longitude': 77.596, 'types': ['pitch', 'cricket'],
            'source': 'openstreetmap_live',
        }], fallback_city='Bengaluru')
        self.assertEqual(created, 1)
        self.assertEqual(grounds[0].city, 'Bengaluru')
        self.assertEqual(grounds[0].size, 'small')

    def test_auto_game_uses_verified_court_and_is_idempotent(self):
        first = self.client.post('/api/v1/ai/matches/auto-create/', {}, format='json')
        second = self.client.post('/api/v1/ai/matches/auto-create/', {}, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.data['created'])
        self.assertEqual(first.data['data']['venue']['public_id'], str(self.venue.public_id))
        self.assertEqual(first.data['data']['court']['public_id'], str(self.court.public_id))
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data['created'])
        self.assertEqual(Game.objects.filter(host=self.user).count(), 1)
        recommendations = self.client.get('/api/v1/ai/matches/')
        self.assertEqual(recommendations.status_code, 200)
        # The host must not be recommended their own game. Other-player
        # eligibility is covered by MatchApiTests above.
        self.assertEqual(recommendations.data, [])


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


class MatchMyMoodTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='mood-user', email='mood@example.com', password='StrongPass!123', latitude=12.9716, longitude=77.5946)
        PlayerProfile.objects.create(user=self.user, matching_mood='energetic', preferred_play_times=['evening'])
        self.sport = Sport.objects.get(name='Badminton')
        UserSport.objects.create(user=self.user, sport=self.sport, skill_level='intermediate', preferred=True)
        self.candidate = User.objects.create_user(username='mood-player', email='mood-player@example.com', password='StrongPass!123', full_name='Mood Player', latitude=12.972, longitude=77.595)
        PlayerProfile.objects.create(user=self.candidate, profile_visibility='public', preferred_play_times=['evening'], matching_mood='social')
        UserSport.objects.create(user=self.candidate, sport=self.sport, skill_level='intermediate')
        self.host = User.objects.create_user(username='mood-host', email='mood-host@example.com', password='StrongPass!123', latitude=12.972, longitude=77.595)
        PlayerProfile.objects.create(user=self.host, profile_visibility='public', matching_mood='energetic')
        UserSport.objects.create(user=self.host, sport=self.sport, skill_level='intermediate')
        venue = Venue.objects.create(name='Mood Arena', address='1 Mood Rd', city='Bengaluru', latitude=12.972, longitude=77.595, opening_time='06:00', closing_time='22:00')
        court = Court.objects.create(venue=venue, sport=self.sport, name='Mood Court', capacity=4, price_per_hour=300)
        starts = timezone.now() + timedelta(days=4)
        self.game = Game.objects.create(game_reference='MOOD-GAME-1', host=self.host, sport=self.sport, venue=venue, court=court,
            game_date=starts.date(), start_time='18:00', end_time='19:00', starts_at=starts,
            ends_at=starts + timedelta(hours=1), min_players=2, max_players=4, visibility='public', status='open', skill_level='intermediate')
        GamePlayer.objects.create(game=self.game, user=self.host)
        self.client.force_authenticate(self.user)

    def test_mood_compatibility_and_score_fixture(self):
        self.assertEqual(mood_compatibility('energetic', 'energetic'), 100)
        self.assertEqual(mood_compatibility('energetic', 'social'), 50)
        self.assertEqual(mood_compatibility('learning', 'energetic'), 0)
        rows = [{'match_score': 80, 'distance_km': 2, 'reasons': []}]
        ranked = apply_mood_ranking(rows, requester_mood='energetic', mood_getter=lambda _: 'energetic', base_key='match_score', reasons_key='reasons')
        self.assertEqual(ranked[0]['match_score'], 82)
        self.assertEqual(ranked[0]['base_score'], 80)
        self.assertIn('You share the same play mood', ranked[0]['reasons'])

    def test_recommendations_are_actual_and_get_filters_do_not_change_preferences(self):
        response = self.client.get('/api/v1/recommendations/match-my-mood/', {'sport': self.sport.id, 'mood': 'relaxed', 'radius': 20, 'availability': 'evening'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['mood'], 'relaxed')
        self.assertTrue(response.data['games'])
        self.assertTrue(response.data['players'])
        self.assertEqual(response.data['games'][0]['game']['public_id'], str(self.game.public_id))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.matching_mood, 'energetic')
        self.assertEqual(self.user.profile.preferred_distance, 15)
        self.assertEqual(self.user.profile.preferred_play_times, ['evening'])
        self.assertIn('ranking_formula', response.data)
        self.assertIn('Your play moods are compatible', response.data['players'][0]['reasons'])

    def test_all_supported_moods_change_recommendation_ranking(self):
        moods = ('relaxed', 'energetic', 'learning', 'social')
        for mood in moods:
            rows = [
                {'id': candidate_mood, 'score': 80, 'distance_km': index, 'reasons': []}
                for index, candidate_mood in enumerate(moods)
            ]
            ranked = apply_mood_ranking(
                rows, requester_mood=mood,
                mood_getter=lambda row: row['id'],
                base_key='score', reasons_key='reasons',
            )
            self.assertEqual(ranked[0]['id'], mood)
            self.assertEqual(ranked[0]['score'], 82)
            self.assertEqual(ranked[0]['mood_compatibility'], 100)
            self.assertIn('You share the same play mood', ranked[0]['reasons'])

    def test_endpoint_uses_selected_mood_to_reorder_persisted_games(self):
        relaxed_host = User.objects.create_user(
            username='relaxed-game-host', email='relaxed-game-host@example.com',
            password='StrongPass!123', latitude=12.972, longitude=77.595,
        )
        PlayerProfile.objects.create(
            user=relaxed_host, matching_mood='relaxed', preferred_play_times=['evening'],
        )
        UserSport.objects.create(user=relaxed_host, sport=self.sport, skill_level='intermediate')
        relaxed_game = Game.objects.create(
            game_reference='MOOD-GAME-RELAXED', host=relaxed_host, sport=self.sport,
            venue=self.game.venue, court=self.game.court, game_date=self.game.game_date,
            start_time=self.game.start_time, end_time=self.game.end_time,
            starts_at=self.game.starts_at, ends_at=self.game.ends_at,
            min_players=2, max_players=4, visibility='public', status='open',
            skill_level='intermediate',
        )
        GamePlayer.objects.create(game=relaxed_game, user=relaxed_host)

        relaxed = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': self.sport.id, 'mood': 'relaxed', 'availability': 'evening',
        })
        energetic = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': self.sport.id, 'mood': 'energetic', 'availability': 'evening',
        })
        self.assertEqual(relaxed.status_code, 200)
        self.assertEqual(energetic.status_code, 200)
        self.assertEqual(relaxed.data['games'][0]['game']['public_id'], str(relaxed_game.public_id))
        self.assertEqual(energetic.data['games'][0]['game']['public_id'], str(self.game.public_id))
        self.assertIn('ranking_formula', relaxed.data)

    def test_missing_host_profile_does_not_break_availability_filter(self):
        self.host.profile.delete()
        response = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': self.sport.id, 'availability': 'evening',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['games']), 1)

    def test_sport_skill_location_and_availability_filters_compose(self):
        combined = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': self.sport.id, 'skill_level': 'intermediate',
            'availability': 'evening', 'latitude': 12.9716,
            'longitude': 77.5946, 'radius': 2,
        })
        self.assertEqual(combined.status_code, 200)
        self.assertEqual([row['game']['public_id'] for row in combined.data['games']], [str(self.game.public_id)])

        wrong_sport = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': Sport.objects.get(name='Tennis').id,
        })
        wrong_skill = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': self.sport.id, 'skill_level': 'advanced',
        })
        wrong_time = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': self.sport.id, 'availability': 'morning',
        })
        outside_radius = self.client.get('/api/v1/recommendations/match-my-mood/', {
            'sport': self.sport.id, 'latitude': 13.5, 'longitude': 77.5, 'radius': 1,
        })
        for response in (wrong_sport, wrong_skill, wrong_time, outside_radius):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['games'], [])

        invalid_mood = self.client.get('/api/v1/recommendations/match-my-mood/', {'mood': 'competitive'})
        incomplete_location = self.client.get('/api/v1/recommendations/match-my-mood/', {'latitude': 12.9})
        self.assertEqual(invalid_mood.status_code, 400)
        self.assertEqual(incomplete_location.status_code, 400)

    def test_blocks_filters_validation_permissions_and_empty(self):
        UserBlock.objects.create(user=self.user, blocked_user=self.host)
        UserBlock.objects.create(user=self.candidate, blocked_user=self.user)
        response = self.client.get('/api/v1/recommendations/match-my-mood/', {'sport': self.sport.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['games'], [])
        self.assertEqual(response.data['players'], [])
        invalid = self.client.get('/api/v1/recommendations/match-my-mood/', {'availability': 'midnight'})
        self.assertEqual(invalid.status_code, 400)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/v1/recommendations/match-my-mood/').status_code, 401)
        self.client.force_authenticate(self.user)
        UserSport.objects.filter(user=self.user).delete()
        empty = self.client.get('/api/v1/recommendations/match-my-mood/')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data['games'], [])
        self.assertEqual(empty.data['players'], [])


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

    def test_venue_name_location_filters_and_active_bookable_inventory(self):
        inactive = Court.objects.create(venue=self.venue, sport=self.sport, name='Closed Court', capacity=4, price_per_hour=250, active=False)
        by_name = self.client.get('/api/v1/venues/search/', {'q': 'Arena', 'sport': 'badminton'})
        by_city = self.client.get('/api/v1/venues/search/', {'q': 'Bengaluru', 'latitude': 12.9716, 'longitude': 77.5946, 'radius': 2})
        no_match = self.client.get('/api/v1/venues/search/', {'q': 'No such venue'})
        malformed = self.client.get('/api/v1/venues/search/', {'q': 'x' * 121})
        self.assertEqual(by_name.status_code, 200)
        self.assertEqual([item['public_id'] for item in by_name.data['results']], [str(self.venue.public_id)])
        self.assertEqual(by_city.status_code, 200)
        self.assertEqual(by_city.data['results'][0]['distance_km'], 0)
        returned_courts=by_city.data['results'][0]['courts']
        self.assertEqual(len(returned_courts),1)
        self.assertEqual(returned_courts[0]['name'],self.court.name)
        self.assertTrue(returned_courts[0]['active'])
        self.assertEqual(returned_courts[0]['price_per_hour'],'400.00')
        self.assertEqual(no_match.data['count'], 0)
        self.assertEqual(malformed.status_code, 400)

    def test_external_ground_catalog_is_never_reported_as_bookable_inventory(self):
        response = self.client.get('/api/v1/grounds/nearby/', {
            'latitude': 12.9716, 'longitude': 77.5946, 'radius': 5,
        })
        self.assertEqual(response.status_code, 200)
        for result in response.data.get('results', []):
            self.assertFalse(result['booking_supported'])
            self.assertEqual(result['verification_status'], 'external_unverified')

    def test_review_eligibility_duplicate_and_deletion_permissions(self):
        completed = CourtBooking.objects.create(user=self.user, court=self.court, starts_at=timezone.now() - timedelta(hours=2), ends_at=timezone.now() - timedelta(hours=1), status='completed')
        cancelled = CourtBooking.objects.create(user=self.user, court=self.court, starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1), status='cancelled')
        review = self.client.post('/api/v1/venues/reviews/', {'booking_id': completed.id, 'rating': 5, 'comment': 'Great'}, format='json')
        duplicate = self.client.post('/api/v1/venues/reviews/', {'booking_id': completed.id, 'rating': 5}, format='json')
        cancelled_response = self.client.post('/api/v1/venues/reviews/', {'booking_id': cancelled.id, 'rating': 4}, format='json')
        self.assertEqual(review.status_code, 201)
        self.venue.refresh_from_db()
        self.assertEqual(float(self.venue.rating), 5.0)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(cancelled_response.status_code, 400)
        self.client.force_authenticate(self.other)
        forbidden = self.client.delete(f"/api/v1/venues/reviews/{review.data['review']['public_id']}/")
        self.assertEqual(forbidden.status_code, 403)
        self.client.force_authenticate(self.user)
        deleted = self.client.delete(f"/api/v1/venues/reviews/{review.data['review']['public_id']}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(VenueReview.objects.exists())
        self.venue.refresh_from_db()
        self.assertEqual(float(self.venue.rating), 0.0)




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

    def test_availability_accepts_returned_public_filters_duration_and_legacy_ids(self):
        public_filter = self.slots(sport=self.sport.slug, court=str(self.court.public_id), duration=30)
        self.assertEqual(public_filter.status_code, 200)
        self.assertEqual(len(public_filter.data['slots']), 6)
        self.assertTrue(all(str(slot['court']['id']) == str(self.court.public_id) for slot in public_filter.data['slots']))
        self.assertEqual(str(public_filter.data['slots'][0]['price']), '300.00')

        legacy_filter = self.slots(sport=str(self.sport.pk), court=str(self.court.pk), duration=120)
        self.assertEqual(legacy_filter.status_code, 200)
        self.assertEqual(len(legacy_filter.data['slots']), 1)

    def test_elapsed_same_day_slots_are_closed_and_rejected_at_booking(self):
        today = timezone.localdate()
        simulated_now = timezone.make_aware(datetime.combine(today, time(10, 15)), ZoneInfo('Asia/Kolkata'))
        with patch('my_app.services.availability.timezone.now', return_value=simulated_now):
            slots = self.client.get(f'/api/v1/venues/{self.venue.public_id}/availability/', {
                'date': today.isoformat(), 'court': str(self.court.public_id), 'duration': 60,
            })
            self.assertEqual(slots.status_code, 200)
            self.assertEqual([item['status'] for item in slots.data['slots']], ['closed', 'closed', 'available'])
            booking = self.client.post('/api/v1/bookings/', {
                'venue_id': str(self.venue.public_id), 'court_id': str(self.court.public_id),
                'booking_date': today.isoformat(), 'start_time': '09:00', 'end_time': '10:00',
            }, format='json')
        self.assertEqual(booking.status_code, 400)
        self.assertFalse(CourtBooking.objects.exists())

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

        blocked_court = Court.objects.create(venue=self.venue, sport=self.sport, name='Blocked Court', capacity=4, price_per_hour=600)
        CourtBlockedPeriod.objects.create(
            court=blocked_court, starts_at=self.open_at, ends_at=self.open_at+timedelta(hours=1),
            type=CourtBlockedPeriod.Type.BLOCKED,
        )
        blocked_slots = self.slots(court=str(blocked_court.public_id)).data['slots']
        self.assertEqual(blocked_slots[0]['status'], 'blocked')

    def test_invalid_duration_closed_venue_and_transactional_conflict(self):
        invalid = self.slots(duration=45)
        self.assertEqual(invalid.status_code, 400)
        self.venue.active = False
        self.venue.save()
        closed = self.slots()
        self.assertEqual(closed.status_code, 200)
        self.assertTrue(all(slot['status'] == 'closed' for slot in closed.data['slots']))
        rejected = self.client.post('/api/v1/bookings/', {
            'venue_id': str(self.venue.public_id), 'court_id': str(self.court.public_id),
            'booking_date': self.date.isoformat(), 'start_time': '09:00', 'end_time': '10:00',
        }, format='json')
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(CourtBooking.objects.filter(venue=self.venue).exists())
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

    def test_unknown_or_demo_venue_does_not_return_availability(self):
        missing = self.client.get(f'/api/v1/venues/00000000-0000-0000-0000-000000000000/availability/', {
            'date': self.date.isoformat(), 'duration': 60,
        })
        self.assertEqual(missing.status_code, 404)
        self.venue.is_demo = True
        self.venue.save(update_fields=['is_demo', 'updated_at'])
        demo = self.slots()
        self.assertEqual(demo.status_code, 404)


class BookingEngineTests(APITestCase):
    def setUp(self):
        # ScopedRateThrottle keys can otherwise leak across isolated API test
        # users when the test runner reuses their auto-incremented IDs.
        cache.clear()
        self.user = User.objects.create_user(username='booker', email='booker@example.com', password='StrongPass!123')
        self.other = User.objects.create_user(username='booker-other', email='booker-other@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.user)
        PlayerProfile.objects.create(user=self.other)
        sport = Sport.objects.get(name='Badminton')
        self.venue = Venue.objects.create(name='Booking Arena', address='3 Road', city='Bengaluru', latitude=12.97, longitude=77.59, opening_time='09:00', closing_time='20:00', owner=self.other)
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
        duplicate = self.create()
        self.assertEqual(duplicate.status_code, 201)
        self.assertEqual(duplicate.data['data']['public_id'], data['public_id'])
        self.assertEqual(CourtBooking.objects.filter(user=self.user, court=self.court).count(), 1)
        self.assertEqual(str(data['final_amount']), '500.00')
        self.assertTrue(data['booking_reference'].startswith('SM-'))
        self.assertTrue(Notification.objects.filter(user=self.user,type='booking_created',data__booking_id=data['public_id']).exists())
        self.assertTrue(Notification.objects.filter(user=self.other,type='booking_created',data__booking_id=data['public_id']).exists())
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/bookings/{data['public_id']}/").status_code, 404)

    def test_booking_quote_uses_public_uuids_and_server_price(self):
        response = self.client.post('/api/v1/bookings/quote/', self.payload(final_amount='1'), format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['venue_name'], 'Booking Arena')
        self.assertEqual(response.data['court_name'], 'Booking Court')
        self.assertEqual(str(response.data['final_amount']), '500.00')

    def test_demo_inventory_is_hidden_and_cannot_be_booked(self):
        demo = Venue.objects.create(
            name='SportMate Demo Arena', address='Test Road', city='Bengaluru',
            latitude=12.97, longitude=77.59, opening_time='09:00', closing_time='20:00',
            active=True, is_demo=True,
        )
        demo_court = Court.objects.create(
            venue=demo, sport=self.court.sport, name='Demo court', capacity=4,
            price_per_hour=500, active=True,
        )
        listed = self.client.get('/api/v1/venues/')
        items = listed.data.get('results', listed.data)
        self.assertNotIn(str(demo.public_id), [str(item['public_id']) for item in items])
        payload = self.payload(venue_id=str(demo.public_id), court_id=str(demo_court.public_id))
        self.assertEqual(self.client.post('/api/v1/bookings/quote/', payload, format='json').status_code, 400)
        self.assertEqual(self.create(venue_id=str(demo.public_id), court_id=str(demo_court.public_id)).status_code, 400)

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
        self.assertTrue(Notification.objects.filter(user=self.other,type='booking_cancelled',data__booking_id=first.data['data']['public_id']).exists())
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

    def test_lifecycle_filters_keep_cancelled_bookings_in_their_own_tab(self):
        upcoming = self.create(start_time='10:00', end_time='11:00')
        cancelled = self.create(start_time='12:00', end_time='13:00')
        cancelled_id = cancelled.data['data']['public_id']
        self.client.post(f'/api/v1/bookings/{cancelled_id}/cancel/', {'reason': 'Changed plans'}, format='json')
        past = CourtBooking.objects.get(public_id=upcoming.data['data']['public_id'])
        past.starts_at = timezone.now() - timedelta(hours=2)
        past.ends_at = timezone.now() - timedelta(hours=1)
        past.status = CourtBooking.Status.COMPLETED
        past.save(update_fields=['starts_at', 'ends_at', 'status', 'updated_at'])

        upcoming_rows = self.client.get('/api/v1/bookings/', {'upcoming': 'true'}).data['results']
        past_rows = self.client.get('/api/v1/bookings/', {'past': 'true'}).data['results']
        cancelled_rows = self.client.get('/api/v1/bookings/', {'status': 'cancelled'}).data['results']

        self.assertEqual(upcoming_rows, [])
        self.assertEqual(len(past_rows), 1)
        self.assertEqual(len(cancelled_rows), 1)


class BookingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        sport = Sport.objects.get(name='Badminton')
        self.venue = Venue.objects.create(
            name='Concurrent Arena', address='Race Road', city='Bengaluru',
            latitude=12.9716, longitude=77.5946,
            opening_time='09:00', closing_time='20:00',
        )
        self.court = Court.objects.create(
            venue=self.venue, sport=sport, name='Race Court', capacity=4,
            price_per_hour=500,
        )
        self.users = [
            User.objects.create_user(username=f'racer-{i}', email=f'racer-{i}@example.com', password='StrongPass!123')
            for i in range(2)
        ]
        self.booking_date = timezone.localdate() + timedelta(days=3)

    def test_simultaneous_players_cannot_book_the_same_court_slot(self):
        barrier = Barrier(2)

        def attempt(user_id):
            from django.db import close_old_connections, connections
            close_old_connections()
            try:
                user = User.objects.get(pk=user_id)
                barrier.wait(timeout=5)
                booking = BookingService.create(
                    user=user,
                    venue_id=self.venue.public_id,
                    court_id=self.court.public_id,
                    booking_date=self.booking_date,
                    start_time=time(10),
                    end_time=time(11),
                )
                return ('created', booking.pk)
            except BookingConflict:
                return ('conflict', None)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, [user.pk for user in self.users]))

        self.assertEqual(sum(result[0] == 'created' for result in results), 1, results)
        self.assertEqual(sum(result[0] == 'conflict' for result in results), 1, results)
        self.assertEqual(CourtBooking.objects.filter(court=self.court).count(), 1)


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

    def test_only_host_can_remove_active_player_and_history_and_chat_membership_are_preserved(self):
        created = self.create_game()
        game_id = created.data['data']['public_id']
        self.client.force_authenticate(self.player)
        self.assertEqual(self.client.post(f'/api/v1/games/{game_id}/join/').status_code, 200)
        self.assertEqual(ConversationMember.objects.filter(conversation__game__public_id=game_id, user=self.player, is_active=True).count(), 1)
        denied = self.client.post(f'/api/v1/games/{game_id}/players/{self.player.id}/remove/')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.host)
        removed = self.client.post(f'/api/v1/games/{game_id}/players/{self.player.id}/remove/', {'reason': 'Schedule changed'}, format='json')
        self.assertEqual(removed.status_code, 200)
        membership = GamePlayer.objects.get(game__public_id=game_id, user=self.player)
        self.assertEqual(membership.status, GamePlayer.Status.REMOVED)
        self.assertIsNotNone(membership.left_at)
        self.assertEqual(Game.objects.get(public_id=game_id).game_players.filter(status=GamePlayer.Status.CONFIRMED).count(), 1)
        self.assertFalse(ConversationMember.objects.filter(conversation__game__public_id=game_id, user=self.player, is_active=True).exists())
        self.assertEqual(self.player.notifications.filter(title='Removed from game').count(), 1)
        self.assertTrue(Message.objects.filter(conversation__game__public_id=game_id, message_type=Message.Type.SYSTEM, content__contains='removed').exists())

    def test_game_remove_rejects_completed_game_and_inactive_member(self):
        created = self.create_game()
        game_id = created.data['data']['public_id']
        self.assertEqual(self.client.post(f'/api/v1/games/{game_id}/players/{self.player.id}/remove/').status_code, 409)
        Game.objects.filter(public_id=game_id).update(
            status=Game.Status.COMPLETED,
            starts_at=timezone.now() - timedelta(hours=2),
            ends_at=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(self.client.post(f'/api/v1/games/{game_id}/players/{self.other.id}/remove/').status_code, 409)

    def test_host_can_edit_and_cancel_game_but_participant_cannot(self):
        created = self.create_game()
        game_id = created.data['data']['public_id']
        self.client.force_authenticate(self.player)
        self.client.post(f'/api/v1/games/{game_id}/join/')
        denied_edit = self.client.patch(f'/api/v1/games/{game_id}/', {'description': 'unauthorized'}, format='json')
        denied_cancel = self.client.delete(f'/api/v1/games/{game_id}/')
        self.assertEqual(denied_edit.status_code, 403)
        self.assertEqual(denied_cancel.status_code, 403)
        self.client.force_authenticate(self.host)
        updated = self.client.patch(f'/api/v1/games/{game_id}/', {'description': 'New meeting point'}, format='json')
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['data']['description'], 'New meeting point')
        self.assertEqual(self.player.notifications.filter(title='Game details updated').count(), 1)
        cancelled = self.client.delete(f'/api/v1/games/{game_id}/')
        self.assertEqual(cancelled.status_code, 204)
        self.assertEqual(Game.objects.get(public_id=game_id).status, Game.Status.CANCELLED)
        self.assertEqual(self.player.notifications.filter(title='Game cancelled').count(), 1)

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


class PlayerRatingApiTests(APITestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(username='rating-reviewer', email='rating-reviewer@example.com', password='StrongPass!123')
        self.player = User.objects.create_user(username='rating-player', email='rating-player@example.com', password='StrongPass!123')
        self.outsider = User.objects.create_user(username='rating-outsider', email='rating-outsider@example.com', password='StrongPass!123')
        for user in (self.reviewer, self.player, self.outsider): PlayerProfile.objects.create(user=user)
        sport = Sport.objects.get(name='Badminton')
        venue = Venue.objects.create(name='Rating Arena', address='7 Rating Road', city='Bengaluru', latitude=12.9716, longitude=77.5946, opening_time='09:00', closing_time='20:00', timezone='Asia/Kolkata')
        court = Court.objects.create(venue=venue, sport=sport, name='Rating Court', capacity=4, price_per_hour=500)
        start = timezone.now() - timedelta(hours=2)
        self.game = Game.objects.create(game_reference='GM-RATING', host=self.reviewer, sport=sport, venue=venue, court=court, game_date=start.date(), start_time=start.time(), end_time=(start + timedelta(hours=1)).time(), starts_at=start, ends_at=start + timedelta(hours=1), min_players=2, max_players=4, status=Game.Status.COMPLETED)
        GamePlayer.objects.create(game=self.game, user=self.reviewer)
        GamePlayer.objects.create(game=self.game, user=self.player)
        self.client.force_authenticate(self.reviewer)

    def payload(self, **changes):
        data = {'game_id': str(self.game.public_id), 'playing_performance': 5, 'teamwork': 4, 'sportsmanship': 5, 'punctuality': 4, 'review': 'A supportive teammate.'}
        data.update(changes)
        return data

    def test_valid_rating_duplicate_and_summary(self):
        url = f'/api/v1/players/{self.player.public_id}/ratings/'
        created = self.client.post(url, self.payload(), format='json')
        duplicate = self.client.post(url, self.payload(), format='json')
        self.assertEqual(created.status_code, 201)
        self.assertEqual(float(created.data['data']['rating']), 4.5)
        self.assertEqual(duplicate.status_code, 400)
        summary = self.client.get(f'/api/v1/players/{self.player.public_id}/rating-summary/')
        self.assertEqual(summary.data['data']['rating_count'], 1)
        self.assertEqual(summary.data['data']['teamwork'], 4.0)

    def test_rating_uses_equal_weight_categories_and_rounds_half_up_on_create_and_edit(self):
        url = f'/api/v1/players/{self.player.public_id}/ratings/'
        created = self.client.post(url, self.payload(playing_performance=3, teamwork=3, sportsmanship=3, punctuality=4), format='json')
        self.assertEqual(created.status_code, 201)
        self.assertEqual(float(created.data['data']['rating']), 3.3)
        edited = self.client.patch(f"/api/v1/ratings/{created.data['data']['public_id']}/", {'playing_performance': 3, 'teamwork': 3, 'sportsmanship': 3, 'punctuality': 4}, format='json')
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(float(edited.data['data']['rating']), 3.3)

    def test_self_outsider_and_invalid_score_are_rejected(self):
        self.assertEqual(self.client.post(f'/api/v1/players/{self.reviewer.public_id}/ratings/', self.payload(), format='json').status_code, 400)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.post(f'/api/v1/players/{self.player.public_id}/ratings/', self.payload(), format='json').status_code, 403)
        self.client.force_authenticate(self.reviewer)
        self.assertEqual(self.client.post(f'/api/v1/players/{self.player.public_id}/ratings/', self.payload(teamwork=6), format='json').status_code, 400)

    def test_incomplete_game_is_not_eligible(self):
        self.game.status = Game.Status.OPEN
        self.game.save(update_fields=['status', 'updated_at'])
        response = self.client.post(f'/api/v1/players/{self.player.public_id}/ratings/', self.payload(), format='json')
        self.assertEqual(response.status_code, 403)

    def test_left_or_removed_participant_cannot_rate_and_completed_game_stats_are_truthful(self):
        GamePlayer.objects.filter(game=self.game, user=self.player).update(status=GamePlayer.Status.LEFT, left_at=timezone.now())
        response = self.client.post(f'/api/v1/players/{self.player.public_id}/ratings/', self.payload(), format='json')
        self.assertEqual(response.status_code, 403)
        stats = self.client.get(f'/api/v1/players/{self.reviewer.public_id}/statistics/').data['data']
        self.assertEqual(stats['games_played'], 1)
        # Game has no persisted score/result, so these must not be fabricated.
        self.assertIsNone(stats['games_won'])
        self.assertIsNone(stats['games_lost'])
        self.assertIsNone(stats['games_drawn'])
        self.assertIsNone(stats['win_rate'])
        self.assertEqual(stats['events_participated'], 0)

    def test_soft_deleted_rating_remains_single_submission_but_not_in_aggregate(self):
        player_url = f'/api/v1/players/{self.player.public_id}/ratings/'
        created = self.client.post(player_url, self.payload(), format='json')
        self.assertEqual(created.status_code, 201)
        rating_id = created.data['data']['public_id']
        self.assertEqual(self.client.delete(f'/api/v1/ratings/{rating_id}/').status_code, 204)
        eligibility = self.client.get(f'/api/v1/players/{self.player.public_id}/rating-eligibility/').data['data']
        self.assertTrue(eligibility[0]['already_rated'])
        self.assertEqual(self.client.post(player_url, self.payload(), format='json').status_code, 400)
        summary = self.client.get(f'/api/v1/players/{self.player.public_id}/rating-summary/').data['data']
        self.assertEqual(summary['rating_count'], 0)

    def test_game_history_is_scoped_to_authenticated_membership(self):
        response = self.client.get('/api/v1/games/history/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['public_id'] for row in response.data['results']], [str(self.game.public_id)])
        self.client.force_authenticate(self.outsider)
        outsider_history = self.client.get('/api/v1/games/history/')
        self.assertEqual(outsider_history.status_code, 200)
        self.assertEqual(outsider_history.data['results'], [])


class MatchReflectionApiTests(APITestCase):
    def setUp(self):
        self.player = User.objects.create_user(username='reflection-player', email='reflection-player@example.com', password='StrongPass!123')
        self.other = User.objects.create_user(username='reflection-other', email='reflection-other@example.com', password='StrongPass!123')
        PlayerProfile.objects.create(user=self.player)
        PlayerProfile.objects.create(user=self.other)
        sport = Sport.objects.get(name='Badminton')
        venue = Venue.objects.create(name='Reflection Arena', address='7 Reflection Road', city='Bengaluru', latitude=12.9716, longitude=77.5946, opening_time='09:00', closing_time='20:00', timezone='Asia/Kolkata')
        court = Court.objects.create(venue=venue, sport=sport, name='Reflection Court', capacity=4, price_per_hour=500)
        start = timezone.now() - timedelta(hours=2)
        self.game = Game.objects.create(game_reference='GM-REFLECT', host=self.player, sport=sport, venue=venue, court=court, game_date=start.date(), start_time=start.time(), end_time=(start + timedelta(hours=1)).time(), starts_at=start, ends_at=start + timedelta(hours=1), min_players=2, max_players=4, status=Game.Status.COMPLETED)
        GamePlayer.objects.create(game=self.game, user=self.player)
        self.client.force_authenticate(self.player)

    def payload(self, **changes):
        data = {'enjoyment': 5, 'teamwork': 4, 'improvement': 3, 'note': 'Good communication today.'}
        data.update(changes)
        return data

    def test_completed_confirmed_participant_can_submit_once_and_history_is_private(self):
        url = f'/api/v1/games/{self.game.public_id}/reflection/'
        created = self.client.post(url, self.payload(), format='json')
        duplicate = self.client.post(url, self.payload(), format='json')
        self.assertEqual(created.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)
        mine = self.client.get('/api/v1/users/me/match-reflections/')
        self.assertEqual(mine.data['data']['summary']['count'], 1)
        self.assertEqual(mine.data['data']['summary']['averages']['teamwork'], 4.0)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/v1/users/me/match-reflections/').data['data']['summary']['count'], 0)
        self.assertEqual(self.client.get(f"/api/v1/match-reflections/{created.data['data']['public_id']}/").status_code, 405)
        self.assertEqual(PlayerRating.objects.count(), 0)

    def test_ineligible_and_invalid_reflections_are_rejected(self):
        self.assertEqual(self.client.post(f'/api/v1/games/{self.game.public_id}/reflection/', self.payload(enjoyment=6), format='json').status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(f'/api/v1/games/{self.game.public_id}/reflection/', self.payload(), format='json').status_code, 403)
        self.client.force_authenticate(self.player)
        self.game.status = Game.Status.OPEN
        self.game.save(update_fields=['status', 'updated_at'])
        self.assertEqual(self.client.post(f'/api/v1/games/{self.game.public_id}/reflection/', self.payload(), format='json').status_code, 403)

    def test_owner_can_edit_for_24_hours_but_others_and_expired_edits_are_rejected(self):
        created = self.client.post(f'/api/v1/games/{self.game.public_id}/reflection/', self.payload(), format='json')
        reflection_id = created.data['data']['public_id']
        self.assertEqual(self.client.patch(f'/api/v1/match-reflections/{reflection_id}/', {'enjoyment': 2}, format='json').status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(f'/api/v1/match-reflections/{reflection_id}/', {'enjoyment': 1}, format='json').status_code, 403)
        self.client.force_authenticate(self.player)
        MatchReflection.objects.filter(public_id=reflection_id).update(created_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(self.client.patch(f'/api/v1/match-reflections/{reflection_id}/', {'enjoyment': 1}, format='json').status_code, 400)

    def test_eligible_discovery_excludes_already_reflected_game(self):
        endpoint = '/api/v1/users/me/match-reflections/eligible/'
        self.assertEqual(len(self.client.get(endpoint).data['data']), 1)
        self.client.post(f'/api/v1/games/{self.game.public_id}/reflection/', self.payload(), format='json')
        self.assertEqual(self.client.get(endpoint).data['data'], [])


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

    def test_create_direct_conversation_with_public_user_id(self):
        response = self.client.post('/api/v1/conversations/', {
            'type': 'one_to_one',
            'user_public_id': str(self.other.public_id),
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['other_user']['public_id'], str(self.other.public_id))

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
        self.assertEqual(len(listed.data['results']), 1)
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

    def test_conversation_messages_contribute_to_unread_count(self):
        created = self.create_direct()
        conversation_id = created.data['data']['public_id']
        self.client.force_authenticate(self.other)
        self.client.post(
            f'/api/v1/conversations/{conversation_id}/messages/',
            {'content': 'New conversation message'}, format='json',
        )
        self.client.force_authenticate(self.user)
        unread = self.client.get('/api/v1/messages/unread/')
        self.assertEqual(unread.status_code, 200)
        self.assertEqual(unread.data['unread'], 1)
        notification = Notification.objects.get(user=self.user, type='message')
        self.assertEqual(notification.data['conversation_id'], conversation_id)
        self.assertFalse(notification.is_read)
        self.client.post(f'/api/v1/conversations/{conversation_id}/read/')
        self.assertEqual(self.client.get('/api/v1/messages/unread/').data['unread'], 0)

    def test_conversation_directory_paginates_and_orders_by_latest_message(self):
        first = self.create_direct()
        first_id = first.data['data']['public_id']
        for index in range(21):
            peer = User.objects.create_user(
                username=f'chat-peer-{index}', email=f'chat-peer-{index}@example.com',
                password='StrongPass!123',
            )
            conversation = Conversation.objects.create(
                conversation_type=Conversation.Type.ONE_TO_ONE,
                participant_one=min(self.user, peer, key=lambda user: user.pk),
                participant_two=max(self.user, peer, key=lambda user: user.pk),
            )
            ConversationMember.objects.create(conversation=conversation, user=self.user)
            ConversationMember.objects.create(conversation=conversation, user=peer)
        self.client.post(
            f'/api/v1/conversations/{first_id}/messages/',
            {'content': 'This conversation was updated most recently.'}, format='json',
        )

        first_page = self.client.get('/api/v1/conversations/?page=1&page_size=20')
        second_page = self.client.get('/api/v1/conversations/?page=2&page_size=20')

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.data['count'], 22)
        self.assertEqual(first_page.data['results'][0]['public_id'], first_id)
        self.assertIsNotNone(first_page.data['next'])
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(first_page.data['results']) + len(second_page.data['results']), 22)

    def test_staff_cannot_access_message_actions_without_conversation_membership(self):
        conversation_id = self.create_direct().data['data']['public_id']
        message = self.client.post(
            f'/api/v1/conversations/{conversation_id}/messages/',
            {'content': 'Private message'}, format='json',
        ).data['data']
        staff = User.objects.create_user(
            username='chat-staff', email='chat-staff@example.com', password='StrongPass!123',
            is_staff=True,
        )
        self.client.force_authenticate(staff)

        response = self.client.patch(
            f"/api/v1/messages/{message['public_id']}/",
            {'content': 'Unauthorized edit'}, format='json',
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Message.objects.get(public_id=message['public_id']).content, 'Private message')

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

    def test_idempotent_retry_and_cursor_history_preserve_order(self):
        conversation_id = self.create_direct().data['data']['public_id']
        retry_id = 'd6ddcc40-7050-4af6-9e98-2427a179ce30'
        first = self.client.post(
            f'/api/v1/conversations/{conversation_id}/messages/',
            {'content': 'first', 'client_message_id': retry_id}, format='json',
        )
        retry = self.client.post(
            f'/api/v1/conversations/{conversation_id}/messages/',
            {'content': 'first', 'client_message_id': retry_id}, format='json',
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 201)
        self.assertEqual(first.data['data']['public_id'], retry.data['data']['public_id'])
        for content in ('second', 'third', 'fourth'):
            self.client.post(f'/api/v1/conversations/{conversation_id}/messages/', {'content': content}, format='json')
        latest = self.client.get(f'/api/v1/conversations/{conversation_id}/messages/?page_size=2')
        self.assertEqual([row['content'] for row in latest.data['results']], ['fourth', 'third'])
        older = self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages/?page_size=2&before={latest.data['next_cursor']}"
        )
        self.assertEqual([row['content'] for row in older.data['results']], ['second', 'first'])
        invalid_cursor = self.client.get(f'/api/v1/conversations/{conversation_id}/messages/?before=not-a-uuid')
        self.assertEqual(invalid_cursor.status_code, 400)
        self.assertEqual(Message.objects.filter(conversation__public_id=conversation_id).count(), 4)
        self.assertEqual(Notification.objects.filter(user=self.other, type='message').count(), 4)

    def test_legacy_message_routes_are_scoped_validated_and_block_aware(self):
        self.client.force_authenticate(self.other)
        Message.objects.create(sender=self.outsider, receiver=self.other, content='Private legacy note')
        self.client.force_authenticate(self.user)
        own_thread = self.client.get(f'/api/v1/messages/{self.other.id}/')
        other_thread = self.client.get(f'/api/v1/messages/{self.outsider.id}/')
        self.assertEqual(own_thread.status_code, 200)
        self.assertEqual(len(own_thread.data['results']), 0)
        self.assertEqual(other_thread.status_code, 200)
        self.assertEqual(len(other_thread.data['results']), 0)
        empty = self.client.post('/api/v1/messages/', {'receiver': self.other.id, 'content': '   '}, format='json')
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(self.client.post('/api/v1/messages/', {'receiver': self.user.id, 'content': 'Hello'}, format='json').status_code, 400)
        UserBlock.objects.create(user=self.user, blocked_user=self.other)
        blocked = self.client.post('/api/v1/messages/', {'receiver': self.other.id, 'content': 'Hello'}, format='json')
        self.assertEqual(blocked.status_code, 403)

    def test_expired_authentication_cannot_read_messages(self):
        conversation_id = self.create_direct().data['data']['public_id']
        self.client.force_authenticate(user=None)
        response = self.client.get(f'/api/v1/conversations/{conversation_id}/messages/')
        self.assertEqual(response.status_code, 401)

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
        self.client.force_authenticate(self.other)
        left = self.client.post(f'/api/v1/games/{game.public_id}/leave/')
        self.assertEqual(left.status_code, 200)
        denied_after_leave = self.client.get(f'/api/v1/conversations/{conversation_id}/messages/')
        self.assertEqual(denied_after_leave.status_code, 403)
        self.assertEqual(Message.objects.filter(conversation__public_id=conversation_id).count(), 2)
        self.assertTrue(Message.objects.filter(conversation__public_id=conversation_id, content='I will be there.').exists())

    def test_cancelled_game_chat_keeps_history_readable_but_rejects_new_messages(self):
        sport = Sport.objects.get(name='Badminton')
        venue = Venue.objects.create(name='Cancelled Chat Arena', address='8 Road', city='Bengaluru', latitude=12.97, longitude=77.59, opening_time='09:00', closing_time='20:00')
        court = Court.objects.create(venue=venue, sport=sport, name='Cancelled Chat Court', capacity=4, price_per_hour=500)
        starts = timezone.now() + timedelta(days=2)
        game = Game.objects.create(game_reference='GM-CHAT-CANCEL', host=self.user, sport=sport, venue=venue, court=court, game_date=timezone.localdate() + timedelta(days=2), start_time='10:00', end_time='11:00', starts_at=starts, ends_at=starts + timedelta(hours=1), min_players=2, max_players=4)
        GamePlayer.objects.create(game=game, user=self.user)
        GamePlayer.objects.create(game=game, user=self.other)
        group = self.client.post('/api/v1/conversations/', {'type': 'GAME_GROUP', 'game_id': str(game.public_id)}, format='json')
        conversation_id = group.data['data']['public_id']
        self.client.force_authenticate(self.other)
        self.client.post(f'/api/v1/conversations/{conversation_id}/messages/', {'content': 'Original history'}, format='json')
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.delete(f'/api/v1/games/{game.public_id}/').status_code, 204)
        self.client.force_authenticate(self.other)
        history = self.client.get(f'/api/v1/conversations/{conversation_id}/messages/')
        self.assertEqual(history.status_code, 200)
        self.assertTrue(any(item['content'] == 'Original history' for item in history.data['results']))
        denied_send = self.client.post(f'/api/v1/conversations/{conversation_id}/messages/', {'content': 'After cancellation'}, format='json')
        self.assertEqual(denied_send.status_code, 400)


class MatchTimePollApiTests(APITestCase):
    def setUp(self):
        self.sport = Sport.objects.get(name='Football')
        self.ground = Ground.objects.create(name='Poll Ground', city='Bengaluru', latitude=12.9716, longitude=77.5946, price_per_hour=300)
        self.organizer = User.objects.create_user(username='poll-organizer', email='poll-organizer@example.com', password='StrongPass!123')
        self.player = User.objects.create_user(username='poll-player', email='poll-player@example.com', password='StrongPass!123')
        self.other = User.objects.create_user(username='poll-other', email='poll-other@example.com', password='StrongPass!123')
        self.match = Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now()+timedelta(days=10), end_date_time=timezone.now()+timedelta(days=10, minutes=90), total_players=4)
        self.match.joined_players.add(self.player)

    def options(self, *times):
        return [{'starts_at': value.isoformat(), 'ends_at': (value+timedelta(minutes=90)).isoformat()} for value in times]

    def create_poll(self, *, options=None, deadline=None):
        self.client.force_authenticate(self.organizer)
        deadline = deadline or timezone.now()+timedelta(days=1)
        options = options or self.options(timezone.now()+timedelta(days=11), timezone.now()+timedelta(days=12))
        return self.client.post(f'/api/v1/matches/{self.match.id}/time-poll/', {'deadline': deadline.isoformat(), 'options': options}, format='json')

    def test_permissions_and_vote_change_do_not_change_match_until_finalized(self):
        self.client.force_authenticate(self.other)
        denied = self.client.post(f'/api/v1/matches/{self.match.id}/time-poll/', {'deadline': (timezone.now()+timedelta(days=1)).isoformat(), 'options': self.options(timezone.now()+timedelta(days=11), timezone.now()+timedelta(days=12))}, format='json')
        self.assertEqual(denied.status_code, 403)
        created = self.create_poll()
        self.assertEqual(created.status_code, 201, created.data)
        poll_id = created.data['data']['public_id']
        options = created.data['data']['options']
        original_schedule = Match.objects.get(pk=self.match.pk).date_time
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': options[0]['public_id']}, format='json').status_code, 403)
        self.client.force_authenticate(self.player)
        vote1 = self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': options[0]['public_id']}, format='json')
        self.assertEqual(vote1.status_code, 200, vote1.data)
        self.assertEqual(Match.objects.get(pk=self.match.pk).date_time, original_schedule)
        vote2 = self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': options[1]['public_id']}, format='json')
        self.assertEqual(vote2.status_code, 200, vote2.data)
        self.assertEqual(MatchTimePollVote.objects.filter(poll__public_id=poll_id, voter=self.player, is_active=True).count(), 1)
        self.assertEqual(MatchTimePollVote.objects.filter(poll__public_id=poll_id, voter=self.player).count(), 2)
        self.client.force_authenticate(self.organizer)
        finalized = self.client.post(f'/api/v1/match-time-polls/{poll_id}/finalize/', {'expected_version': 1}, format='json')
        self.assertEqual(finalized.status_code, 200, finalized.data)
        self.assertEqual(finalized.data['data']['status'], 'finalized')
        self.assertIn('match', finalized.data)
        self.assertEqual(Match.objects.get(pk=self.match.pk).date_time, MatchTimePollOption.objects.get(public_id=options[1]['public_id']).starts_at)
        self.assertTrue(Notification.objects.filter(user=self.player, title='Match time finalized').exists())

    def test_tie_requires_organizer_choice_and_persists_tie_state(self):
        self.match.joined_players.add(self.other)
        created = self.create_poll()
        poll_id, options = created.data['data']['public_id'], created.data['data']['options']
        for user, option in ((self.player, options[0]), (self.other, options[1])):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': option['public_id']}, format='json').status_code, 200)
        original = Match.objects.get(pk=self.match.pk).date_time
        self.client.force_authenticate(self.organizer)
        tied = self.client.post(f'/api/v1/match-time-polls/{poll_id}/finalize/', {}, format='json')
        self.assertEqual(tied.status_code, 200, tied.data)
        self.assertTrue(tied.data['tie_requires_decision'])
        self.assertEqual(tied.data['data']['status'], 'tie')
        self.assertEqual(Match.objects.get(pk=self.match.pk).date_time, original)
        picked = self.client.post(f'/api/v1/match-time-polls/{poll_id}/finalize/', {'option_id': options[0]['public_id'], 'expected_version': 2}, format='json')
        self.assertEqual(picked.status_code, 200, picked.data)

    def test_deadline_expiry_and_conflicting_schedule_leave_poll_open(self):
        expired = self.create_poll(deadline=timezone.now()-timedelta(minutes=1))
        self.assertEqual(expired.status_code, 400)
        deadline = timezone.now() + timedelta(days=1)
        before_deadline = self.create_poll(
            deadline=deadline,
            options=self.options(deadline - timedelta(minutes=1), timezone.now()+timedelta(days=12)),
        )
        self.assertEqual(before_deadline.status_code, 400)
        created = self.create_poll()
        poll_id, options = created.data['data']['public_id'], created.data['data']['options']
        self.client.force_authenticate(self.player)
        self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': options[0]['public_id']}, format='json')
        conflicting_start = options[0]['starts_at']
        Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.other, date_time=conflicting_start, end_date_time=conflicting_start+timedelta(hours=2), total_players=2)
        self.client.force_authenticate(self.organizer)
        conflict = self.client.post(f'/api/v1/match-time-polls/{poll_id}/finalize/', {}, format='json')
        self.assertEqual(conflict.status_code, 400)
        self.assertEqual(MatchTimePoll.objects.get(public_id=poll_id).status, MatchTimePoll.Status.OPEN)

    def test_insufficient_votes_and_cancelled_match_close_poll_and_reject_votes(self):
        created = self.create_poll()
        poll_id = created.data['data']['public_id']
        option_id = created.data['data']['options'][0]['public_id']
        self.client.force_authenticate(self.organizer)
        insufficient = self.client.post(f'/api/v1/match-time-polls/{poll_id}/finalize/', {}, format='json')
        self.assertEqual(insufficient.status_code, 400)
        self.assertEqual(MatchTimePoll.objects.get(public_id=poll_id).status, MatchTimePoll.Status.OPEN)

        cancelled = self.client.post(f'/api/v1/matches/{self.match.id}/cancel/', {}, format='json')
        self.assertEqual(cancelled.status_code, 200)
        poll = MatchTimePoll.objects.get(public_id=poll_id)
        self.assertEqual(poll.status, MatchTimePoll.Status.CLOSED)
        self.assertEqual(poll.votes.count(), 0)
        self.client.force_authenticate(self.player)
        late_vote = self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': option_id}, format='json')
        self.assertEqual(late_vote.status_code, 400)

    def test_finalization_rejects_an_option_that_has_passed(self):
        created = self.create_poll(options=self.options(
            timezone.now()+timedelta(days=2), timezone.now()+timedelta(days=3),
        ))
        poll_id, option = created.data['data']['public_id'], created.data['data']['options'][0]
        self.client.force_authenticate(self.player)
        self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': option['public_id']}, format='json')
        self.client.force_authenticate(self.organizer)
        with patch('my_app.services.match_time_polls.timezone.now', return_value=timezone.now()+timedelta(days=5)):
            response = self.client.post(f'/api/v1/match-time-polls/{poll_id}/finalize/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MatchTimePoll.objects.get(public_id=poll_id).status, MatchTimePoll.Status.OPEN)

    def test_expired_poll_disables_voting_but_keeps_results_available_to_finalize(self):
        created = self.create_poll()
        poll_id = created.data['data']['public_id']
        self.client.force_authenticate(self.player)
        self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': created.data['data']['options'][0]['public_id']}, format='json')
        poll = MatchTimePoll.objects.get(public_id=poll_id)
        with patch('my_app.services.match_time_polls.timezone.now', return_value=poll.deadline+timedelta(minutes=1)):
            state = MatchTimePollService.serialize(poll, user=self.organizer)
        self.assertEqual(state['status'], MatchTimePoll.Status.OPEN)
        self.assertFalse(state['voting_open'])


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
        self.assertFalse(community.is_club)
        self.assertEqual(community.member_count, 1)
        self.assertEqual(community.members.get(user=self.owner).role, CommunityMember.Role.OWNER)
        self.client.force_authenticate(self.member)
        joined = self.client.post(f'/api/v1/communities/{community_id}/join/')
        self.assertTrue(joined.data['joined'])
        self.assertEqual(Community.objects.get(public_id=community_id).member_count, 2)
        duplicate_join = self.client.post(f'/api/v1/communities/{community_id}/join/')
        self.assertFalse(duplicate_join.data['changed'])
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
        self.client.post(f'/api/v1/communities/{community_id}/leave/')
        self.assertEqual(Community.objects.get(public_id=community_id).member_count, 1)

    def test_community_update_and_owner_only_archive_preserve_record(self):
        created = self.create(is_club=False)
        community_id = created.data['data']['public_id']
        self.client.force_authenticate(self.member)
        denied = self.client.delete(f'/api/v1/communities/{community_id}/')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.owner)
        changed = self.client.patch(f'/api/v1/communities/{community_id}/', {'name': 'Updated community'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertEqual(changed.data['data']['name'], 'Updated community')
        archived = self.client.delete(f'/api/v1/communities/{community_id}/')
        self.assertEqual(archived.status_code, 204)
        self.assertTrue(Community.objects.filter(public_id=community_id, is_active=False).exists())

    def test_club_list_details_and_members_require_membership_for_club(self):
        created = self.create(is_club=True)
        club_id = created.data['data']['public_id']
        self.client.force_authenticate(self.member)
        detail = self.client.get(f'/api/v1/communities/{club_id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertIsNone(detail.data['data']['member_role'])
        self.assertEqual(self.client.get(f'/api/v1/communities/{club_id}/members/').status_code, 403)
        self.assertTrue(self.client.post(f'/api/v1/communities/{club_id}/join/').data['joined'])
        self.assertEqual(self.client.get(f'/api/v1/communities/{club_id}/').data['data']['member_count'], 2)
        self.assertEqual(self.client.get(f'/api/v1/communities/{club_id}/members/').status_code, 200)
        self.assertTrue(Community.objects.filter(is_club=True, members__user=self.member, members__status=CommunityMember.Status.ACTIVE).exists())
        self.client.force_authenticate(self.owner)
        detail = self.client.get(f'/api/v1/communities/{club_id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data['data']['member_role'], 'owner')

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

    def test_closed_or_inactive_clubs_reject_new_members_and_filters_validate(self):
        closed = self.create(name='Closed club', is_club=True, membership_open=False)
        club_id = closed.data['data']['public_id']
        self.client.force_authenticate(self.member)
        response = self.client.post(f'/api/v1/communities/{club_id}/join/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Community.objects.get(public_id=club_id).member_count, 1)
        self.client.force_authenticate(self.owner)
        self.client.delete(f'/api/v1/communities/{club_id}/')
        self.client.force_authenticate(self.member)
        self.assertEqual(self.client.post(f'/api/v1/communities/{club_id}/join/').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/communities/?sport=not-a-number').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/communities/?clubs=true&communities=true').status_code, 400)

    def test_club_discovery_filters_by_sport_and_city(self):
        created = self.create(is_club=True, city='Bengaluru')
        response = self.client.get(f'/api/v1/communities/?clubs=true&sport={self.sport.id}&city=Bengaluru')
        self.assertEqual(response.status_code, 200)
        rows = response.data.get('results', response.data.get('data', []))
        self.assertEqual([row['public_id'] for row in rows], [created.data['data']['public_id']])

    def test_linked_club_events_and_games_use_authoritative_records(self):
        club = self.create(is_club=True).data['data']
        venue = Venue.objects.create(name='Community Venue', address='1 Road', city='Bengaluru', latitude=12.97, longitude=77.59, opening_time='09:00', closing_time='20:00')
        court = Court.objects.create(venue=venue, sport=self.sport, name='Community Court', capacity=4, price_per_hour=300)
        event_date = timezone.localdate() + timedelta(days=7)
        event_data = {
            'title': 'Club practice', 'sport_id': self.sport.id,
            'community_id': club['public_id'], 'venue_id': str(venue.public_id),
            'court_id': str(court.public_id), 'event_date': event_date.isoformat(),
            'start_time': '10:00', 'end_time': '11:00', 'maximum_participants': 8,
            'registration_deadline': (timezone.now() + timedelta(days=5)).isoformat(),
            'visibility': 'community',
        }
        self.client.force_authenticate(self.member)
        denied = self.client.post('/api/v1/events/', event_data, format='json')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.owner)
        created_event = self.client.post('/api/v1/events/', event_data, format='json')
        self.assertEqual(created_event.status_code, 201, created_event.data)
        starts_at = timezone.now() + timedelta(days=7)
        game = Game.objects.create(
            game_reference='GM-CLUB-LINK', host=self.owner, sport=self.sport, venue=venue, court=court,
            game_date=event_date, start_time='12:00', end_time='13:00', starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1), min_players=2, max_players=4,
            visibility=Game.Visibility.PUBLIC, status=Game.Status.OPEN,
        )
        GamePlayer.objects.create(game=game, user=self.owner)
        post = self.client.post(f"/api/v1/communities/{club['public_id']}/posts/", {
            'content': 'Join our club match', 'post_type': 'game', 'game_id': str(game.public_id),
        }, format='json')
        self.assertEqual(post.status_code, 201)
        activities = self.client.get(f"/api/v1/clubs/{club['public_id']}/activities/")
        self.assertEqual(activities.status_code, 200)
        self.assertEqual([item['public_id'] for item in activities.data['events']], [created_event.data['data']['public_id']])
        self.assertEqual(activities.data['games'][0]['game']['public_id'], str(game.public_id))

    def test_club_roles_invites_and_ownership_transfer_permissions(self):
        created = self.create(is_club=True)
        self.assertEqual(created.status_code, 201)
        club_id = created.data['data']['public_id']
        self.client.force_authenticate(self.member)
        self.assertTrue(self.client.post(f'/api/v1/communities/{club_id}/join/').data['joined'])
        denied_role = self.client.patch(f'/api/v1/clubs/{club_id}/members/{self.member.id}/role/', {'role': 'captain'}, format='json')
        self.assertEqual(denied_role.status_code, 403)
        self.client.force_authenticate(self.owner)
        captain = self.client.patch(f'/api/v1/clubs/{club_id}/members/{self.member.id}/role/', {'role': 'captain'}, format='json')
        self.assertEqual(captain.status_code, 200)
        self.assertEqual(captain.data['data']['role'], 'captain')
        invited = self.client.post(f'/api/v1/clubs/{club_id}/invitations/', {'user_id': self.other.id}, format='json')
        duplicate = self.client.post(f'/api/v1/clubs/{club_id}/invitations/', {'user_id': self.other.id}, format='json')
        self.assertEqual(invited.status_code, 201)
        self.assertEqual(duplicate.status_code, 400)
        transferred = self.client.post(f'/api/v1/clubs/{club_id}/transfer-ownership/', {'user_id': self.member.id}, format='json')
        self.assertEqual(transferred.status_code, 200)
        self.assertEqual(Community.objects.get(public_id=club_id).owner_id, self.member.id)
        self.assertEqual(CommunityMember.objects.get(community__public_id=club_id, user=self.owner).role, CommunityMember.Role.CAPTAIN)

    def test_last_owner_cannot_be_removed_and_private_membership_is_protected(self):
        created = self.create(is_club=True, visibility='private')
        club_id = created.data['data']['public_id']
        self.client.force_authenticate(self.member)
        self.assertTrue(self.client.post(f'/api/v1/communities/{club_id}/join/').data['pending'])
        self.client.force_authenticate(self.owner)
        removed = self.client.post(f'/api/v1/communities/{club_id}/members/{self.owner.id}/remove/')
        self.assertEqual(removed.status_code, 400)
        self.assertEqual(self.client.get(f'/api/v1/clubs/{club_id}/activities/').status_code, 200)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/v1/clubs/{club_id}/activities/').status_code, 403)


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
        duplicate = self.client.post(f'/api/v1/events/{event.public_id}/register/')
        self.assertEqual(duplicate.data['data']['status'], 'registered')
        self.assertEqual(EventParticipant.objects.filter(event=event, user=self.first).count(), 1)
        self.client.force_authenticate(self.second)
        self.assertEqual(self.client.post(f'/api/v1/events/{event.public_id}/register/').data['data']['status'], 'waitlisted')
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.post(f'/api/v1/events/{event.public_id}/leave/').status_code, 200)
        self.assertEqual(EventParticipant.objects.get(event=event, user=self.second).status, EventParticipant.Status.REGISTERED)

    def test_event_filters_date_sport_city_type_and_registration(self):
        created = self.create_event()
        self.assertEqual(created.status_code, 201, created.data)
        event = Event.objects.get(public_id=created.data['data']['public_id'])
        self.client.force_authenticate(self.first)
        registered = self.client.post(f'/api/v1/events/{event.public_id}/register/')
        self.assertEqual(registered.status_code, 200)
        day = (timezone.localdate()+timedelta(days=7)).isoformat()
        response = self.client.get('/api/v1/calendar/', {'date_from':day,'date_to':day,'sport_id':self.sport.id,'location':'Bengal','registration_status':'registered'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['data'][0]['kind'], 'event')
        self.assertEqual(response.data['data'][0]['registration_status'], 'registered')
        outside = self.client.get('/api/v1/events/', {'date_from':(timezone.localdate()+timedelta(days=8)).isoformat(),'date_to':(timezone.localdate()+timedelta(days=8)).isoformat()})
        self.assertEqual(outside.status_code, 200)
        self.assertEqual(outside.data['count'] if isinstance(outside.data,dict) and 'count' in outside.data else len(outside.data), 0)

    def test_calendar_lifecycle_filters_and_private_participant_visibility(self):
        created = self.create_event()
        event = Event.objects.get(public_id=created.data['data']['public_id'])
        self.client.force_authenticate(self.first)
        self.assertEqual(self.client.get('/api/v1/events/', {'lifecycle': 'upcoming'}).status_code, 200)
        invalid = self.client.get('/api/v1/calendar/', {'lifecycle': 'future-ish'})
        self.assertEqual(invalid.status_code, 400)
        event.starts_at = timezone.now() - timedelta(minutes=10)
        event.ends_at = timezone.now() + timedelta(hours=1)
        event.save(update_fields=['starts_at', 'ends_at', 'updated_at'])
        ongoing = self.client.get('/api/v1/calendar/', {'lifecycle': 'ongoing'})
        self.assertEqual([row['public_id'] for row in ongoing.data['data']], [str(event.public_id)])
        event.ends_at = timezone.now() - timedelta(minutes=1)
        event.save(update_fields=['ends_at', 'updated_at'])
        past = self.client.get('/api/v1/calendar/', {'lifecycle': 'past'})
        self.assertEqual([row['public_id'] for row in past.data['data']], [str(event.public_id)])
        private = Event.objects.create(
            event_reference='EV-PRIVATE-1', title='Private event', organizer=self.organizer,
            sport=self.sport, venue=self.venue, event_date=event.event_date,
            start_time=event.start_time, end_time=event.end_time,
            starts_at=event.starts_at, ends_at=event.ends_at,
            maximum_participants=4, minimum_participants=1,
            visibility=Event.Visibility.PRIVATE, registration_deadline=event.registration_deadline,
        )
        denied = self.client.get(f'/api/v1/events/{private.public_id}/participants/')
        self.assertEqual(denied.status_code, 404)
        # Public event rosters remain available to authenticated users.
        self.assertEqual(self.client.get(f'/api/v1/events/{event.public_id}/participants/').status_code, 200)

    def test_event_schedule_edit_rechecks_court_conflicts(self):
        created = self.create_event()
        event = Event.objects.get(public_id=created.data['data']['public_id'])
        blocked_start = event.event_date
        CourtBlockedPeriod.objects.create(
            court=self.court, starts_at=event.starts_at + timedelta(days=1),
            ends_at=event.ends_at + timedelta(days=1), reason='Maintenance',
        )
        response = self.client.patch(f'/api/v1/events/{event.public_id}/', {
            'event_date': (blocked_start + timedelta(days=1)).isoformat(),
        }, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        event.refresh_from_db()
        self.assertEqual(event.event_date, blocked_start)

    def test_community_visibility_requires_linked_community(self):
        payload = self.create_event().data['data']
        event_id = payload['public_id']
        response = self.client.patch(f'/api/v1/events/{event_id}/', {'visibility': 'community'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_event_detail_edit_permissions_deadline_and_reminder_delivery(self):
        created=self.create_event();event=Event.objects.get(public_id=created.data['data']['public_id'])
        self.client.force_authenticate(self.first)
        denied=self.client.patch(f'/api/v1/events/{event.public_id}/',{'title':'Changed by participant'},format='json')
        self.assertEqual(denied.status_code,403)
        no_reminder=self.client.post(f'/api/v1/events/{event.public_id}/reminder/',{'minutes_before':60},format='json')
        self.assertEqual(no_reminder.status_code,403)
        self.client.post(f'/api/v1/events/{event.public_id}/register/')
        reminder=self.client.post(f'/api/v1/events/{event.public_id}/reminder/',{'minutes_before':60},format='json')
        self.assertEqual(reminder.status_code,200,reminder.data)
        record=EventReminder.objects.get(event=event,user=self.first)
        self.assertEqual(EventReminderService.deliver_due(now=record.remind_at+timedelta(seconds=1)),1)
        self.assertIsNotNone(EventReminder.objects.get(pk=record.pk).sent_at)
        self.assertTrue(Notification.objects.filter(user=self.first,title='Event reminder').exists())
        # Closed registration deadlines are enforced in the service even if a
        # client submits directly against a still-open event.
        event.registration_deadline=timezone.now()-timedelta(seconds=1);event.save(update_fields=['registration_deadline','updated_at'])
        self.client.force_authenticate(self.second)
        self.assertEqual(self.client.post(f'/api/v1/events/{event.public_id}/register/').status_code,400)

    def test_event_organizer_update_and_delete_cancel_preserve_event(self):
        created = self.create_event()
        event = Event.objects.get(public_id=created.data['data']['public_id'])
        self.client.force_authenticate(self.first)
        self.client.post(f'/api/v1/events/{event.public_id}/register/')
        self.assertEqual(self.client.delete(f'/api/v1/events/{event.public_id}/').status_code, 403)
        self.client.force_authenticate(self.organizer)
        changed = self.client.patch(f'/api/v1/events/{event.public_id}/', {'title': 'Updated event', 'description': 'New details'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        self.assertTrue(Notification.objects.filter(user=self.first,type='event_updated',data__event_id=str(event.public_id)).exists())
        event.refresh_from_db()
        self.assertEqual(event.title, 'Updated event')
        cancelled = self.client.delete(f'/api/v1/events/{event.public_id}/')
        self.assertEqual(cancelled.status_code, 204)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.CANCELLED)
        self.assertTrue(Notification.objects.filter(user=self.first, type='event_cancelled', data__event_id=str(event.public_id)).exists())
        self.assertEqual(EventParticipant.objects.get(event=event, user=self.first).status, EventParticipant.Status.CANCELLED)

    def test_tournament_crud_is_organizer_only_and_delete_cancels(self):
        start = timezone.localdate() + timedelta(days=7)
        payload = {'name': 'City Cup', 'sport_id': self.sport.id, 'venue_id': str(self.venue.public_id), 'start_date': start.isoformat(), 'end_date': (start + timedelta(days=1)).isoformat(), 'registration_deadline': (timezone.now() + timedelta(days=2)).isoformat(), 'maximum_teams': 4}
        created = self.client.post('/api/v1/tournaments/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        tournament_id = created.data['data']['public_id']
        self.client.force_authenticate(self.first)
        denied = self.client.patch(f'/api/v1/tournaments/{tournament_id}/', {'name': 'Not allowed'}, format='json')
        self.assertEqual(denied.status_code, 403)
        self.client.force_authenticate(self.organizer)
        updated = self.client.patch(f'/api/v1/tournaments/{tournament_id}/', {'name': 'City Cup Updated'}, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['name'], 'City Cup Updated')
        deleted = self.client.delete(f'/api/v1/tournaments/{tournament_id}/')
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(Tournament.objects.get(public_id=tournament_id).status, Tournament.Status.CANCELLED)

    def test_tournament_can_use_a_nearby_ground(self):
        nearby = SportsGround.objects.create(
            osm_id='nearby-tournament-ground', name='Community Cricket Ground',
            ground_type='pitch', sport='cricket', latitude=12.9716,
            longitude=77.5946,
        )
        start = timezone.localdate() + timedelta(days=7)
        response = self.client.post('/api/v1/tournaments/', {
            'name': 'Ground Cup', 'sport_id': self.sport.id,
            'nearby_ground_id': nearby.id, 'start_date': start.isoformat(),
            'end_date': (start + timedelta(days=1)).isoformat(),
            'registration_deadline': (timezone.now() + timedelta(days=2)).isoformat(),
            'maximum_teams': 4,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        tournament = Tournament.objects.get(public_id=response.data['data']['public_id'])
        self.assertIsNone(tournament.venue)
        self.assertEqual(tournament.nearby_ground, nearby)
        self.assertEqual(Venue.objects.filter(source_nearby_ground=nearby).count(), 0)
        self.assertIsNone(response.data['data']['venue'])
        self.assertEqual(response.data['data']['nearby_ground_id'], nearby.id)
        self.assertEqual(response.data['data']['venue_name'], nearby.name)

    def test_tournament_registration_uses_team_records_and_enforces_deadline_capacity(self):
        start=timezone.localdate()+timedelta(days=5)
        tournament=Tournament.objects.create(tournament_reference='TR-REG',name='Open Tournament',organizer=self.organizer,sport=self.sport,venue=self.venue,start_date=start,end_date=start+timedelta(days=1),registration_deadline=timezone.now()+timedelta(days=3),maximum_teams=2)
        self.client.force_authenticate(self.first)
        joined=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/register/',{'team_name':'First Team'},format='json')
        self.assertEqual(joined.status_code,201,joined.data)
        self.assertEqual(TournamentTeamMember.objects.filter(team__tournament=tournament,user=self.first,status='active').count(),1)
        duplicate=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/register/',{'team_name':'Ignored Duplicate'},format='json')
        self.assertEqual(duplicate.status_code,200)
        self.assertFalse(duplicate.data['created'])
        self.client.force_authenticate(self.second)
        self.assertEqual(self.client.post(f'/api/v1/tournaments/{tournament.public_id}/register/',{'team_name':'Second Team'},format='json').status_code,201)
        third=User.objects.create_user(username='event-third',email='event-third@example.com',password='StrongPass!123')
        self.client.force_authenticate(third)
        full=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/register/',{'team_name':'Third Team'},format='json')
        self.assertEqual(full.status_code,400)
        detail=self.client.get(f'/api/v1/tournaments/{tournament.public_id}/')
        self.assertEqual(detail.data['registered_team_count'],2)
        self.assertEqual(detail.data['available_team_slots'],0)
        self.assertFalse(detail.data['registration_eligible'])

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

    def test_league_round_robin_ties_update_standings_and_double_elimination_is_rejected(self):
        start=timezone.localdate()+timedelta(days=10)
        tournament=Tournament.objects.create(tournament_reference='TR-LEAGUE',name='League',organizer=self.organizer,sport=self.sport,venue=self.venue,start_date=start,end_date=start+timedelta(days=2),registration_deadline=timezone.now()+timedelta(days=8),maximum_teams=3,format=Tournament.Format.LEAGUE)
        teams=[TournamentService.create_team(tournament=tournament,creator=self.organizer,name=f'League {i}') for i in range(3)]
        matches=TournamentBracketService.generate(tournament=tournament,actor=self.organizer)
        self.assertEqual(len(matches),3)
        self.assertEqual([tournament.matches.filter(round_number=round_no).count() for round_no in (1,2,3)],[1,1,1])
        tied=TournamentMatchService.result(match=matches[0],actor=self.organizer,score_a=1,score_b=1)
        self.assertIsNone(tied.winner_id)
        rows=TournamentStandingsService.standings(tournament)
        self.assertEqual(sum(row['points'] for row in rows),2)
        self.assertEqual(sum(row['draws'] for row in rows),2)
        for match in matches[1:]: TournamentMatchService.result(match=match,actor=self.organizer,score_a=2,score_b=0)
        tournament.refresh_from_db()
        self.assertEqual(tournament.status,Tournament.Status.COMPLETED)
        payload={'name':'Bad Format','sport_id':self.sport.id,'venue_id':str(self.venue.public_id),'start_date':start.isoformat(),'end_date':start.isoformat(),'registration_deadline':(timezone.now()+timedelta(days=2)).isoformat(),'maximum_teams':4,'format':'double_elimination'}
        rejected=self.client.post('/api/v1/tournaments/',payload,format='json')
        self.assertEqual(rejected.status_code,400)

    def test_tournament_result_permissions_duplicates_and_cancellation_preserve_completed_results(self):
        start=timezone.localdate()+timedelta(days=10)
        tournament=Tournament.objects.create(tournament_reference='TR-RESULT',name='Results Cup',organizer=self.organizer,sport=self.sport,venue=self.venue,start_date=start,end_date=start+timedelta(days=1),registration_deadline=timezone.now()+timedelta(days=8),maximum_teams=4)
        teams=[TournamentService.create_team(tournament=tournament,creator=self.organizer,name=f'Result Team {i}') for i in range(4)]
        match=TournamentBracketService.generate(tournament=tournament,actor=self.organizer).get(round_number=1,match_number=1)
        self.client.force_authenticate(self.first)
        denied=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/matches/{match.public_id}/result/',{'score_a':3,'score_b':1},format='json')
        self.assertEqual(denied.status_code,403)
        self.client.force_authenticate(self.organizer)
        invalid=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/matches/{match.public_id}/result/',{'score_a':1,'score_b':1},format='json')
        self.assertEqual(invalid.status_code,400)
        saved=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/matches/{match.public_id}/result/',{'score_a':3,'score_b':1},format='json')
        self.assertEqual(saved.status_code,200,saved.data)
        duplicate=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/matches/{match.public_id}/result/',{'score_a':0,'score_b':4},format='json')
        self.assertEqual(duplicate.status_code,400)
        self.assertEqual(TournamentMatch.objects.get(pk=match.pk).score_a,3)
        completed=tournament.matches.get(status=TournamentMatch.Status.COMPLETED)
        TournamentService.cancel(tournament=tournament,actor=self.organizer)
        completed.refresh_from_db()
        self.assertEqual(completed.status,TournamentMatch.Status.COMPLETED)
        self.assertEqual(completed.winner_id,teams[0].id)
        self.assertEqual(tournament.matches.filter(status=TournamentMatch.Status.CANCELLED).count(),2)

    def test_bracket_generation_and_results_are_rejected_after_tournament_cancellation(self):
        start=timezone.localdate()+timedelta(days=10)
        tournament=Tournament.objects.create(tournament_reference='TR-CANCEL-1',name='Cancelled Cup',organizer=self.organizer,sport=self.sport,venue=self.venue,start_date=start,end_date=start+timedelta(days=1),registration_deadline=timezone.now()+timedelta(days=8),maximum_teams=2)
        for i in range(2): TournamentService.create_team(tournament=tournament,creator=self.organizer,name=f'Cancelled Team {i}')
        TournamentBracketService.generate(tournament=tournament,actor=self.organizer)
        pending=tournament.matches.get()
        TournamentService.cancel(tournament=tournament,actor=self.organizer)
        pending.refresh_from_db()
        self.assertEqual(pending.status,TournamentMatch.Status.CANCELLED)
        with self.assertRaises(ValidationError): TournamentMatchService.result(match=pending,actor=self.organizer,score_a=2,score_b=0)

    def test_knockout_winners_advance_from_semifinals_to_final(self):
        start = timezone.localdate() + timedelta(days=10)
        tournament = Tournament.objects.create(
            tournament_reference='TR-KNOCKOUT', name='Knockout Cup',
            organizer=self.organizer, sport=self.sport, venue=self.venue,
            start_date=start, end_date=start + timedelta(days=1),
            registration_deadline=timezone.now() + timedelta(days=8),
            maximum_teams=4,
        )
        teams = [
            TournamentService.create_team(
                tournament=tournament, creator=self.organizer, name=name)
            for name in ['Alpha', 'Bravo', 'Comet', 'Delta']
        ]
        TournamentBracketService.generate(tournament=tournament, actor=self.organizer)
        semifinals = list(tournament.matches.filter(round_number=1).order_by('match_number'))
        self.assertEqual(len(semifinals), 2)
        self.assertEqual(tournament.matches.filter(round_number=2).count(), 1)
        TournamentMatchService.result(
            match=semifinals[0], actor=self.organizer, score_a=2, score_b=1)
        final = tournament.matches.get(round_number=2, match_number=1)
        self.assertEqual(final.team_a, teams[0])
        TournamentMatchService.result(
            match=semifinals[1], actor=self.organizer, score_a=3, score_b=1)
        final.refresh_from_db()
        self.assertEqual(final.team_b, teams[2])
        TournamentMatchService.result(
            match=final, actor=self.organizer, score_a=2, score_b=0)
        tournament.refresh_from_db()
        teams[0].refresh_from_db()
        self.assertEqual(tournament.status, Tournament.Status.COMPLETED)
        self.assertEqual(teams[0].status, TournamentTeam.Status.CHAMPION)


class CollegeNetworkApiTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='college-platform-admin', email='college-platform-admin@example.com', password='StrongPass!123')
        self.admin.groups.add(Group.objects.create(name='SportMate Admins'))
        self.first_admin = User.objects.create_user(username='college-first-admin', email='college-first-admin@example.com', password='StrongPass!123')
        self.second_admin = User.objects.create_user(username='college-second-admin', email='college-second-admin@example.com', password='StrongPass!123')
        self.first_player = User.objects.create_user(username='college-first-player', email='college-first-player@example.com', password='StrongPass!123')
        self.outsider = User.objects.create_user(username='college-outsider', email='college-outsider@example.com', password='StrongPass!123')
        for user in (self.first_admin, self.second_admin, self.first_player, self.outsider):
            PlayerProfile.objects.create(user=user)
        self.sport = Sport.objects.get(name='Football')
        self.venue = Venue.objects.create(name='College Venue', address='Campus', city='Bengaluru', latitude=12.97, longitude=77.59, opening_time='06:00', closing_time='23:00')

    def submit_college(self, user, name):
        self.client.force_authenticate(user)
        response = self.client.post('/api/v1/colleges/', {'name': name, 'city': 'Bengaluru', 'approved_email_domain': f'{name.lower().replace(" ", "")}.edu.in', 'verification_evidence': 'https://example.edu/verification'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return College.objects.get(public_id=response.data['data']['public_id'])

    def verify(self, college):
        self.client.force_authenticate(self.admin)
        return self.client.post(f'/api/v1/admin/colleges/{college.public_id}/verification/', {'approve': True}, format='json')

    def activate_player(self, college, player):
        CollegeMembership.objects.create(college=college, user=player, role=CollegeMembership.Role.PLAYER, status=CollegeMembership.Status.ACTIVE)

    def test_unverified_college_is_not_discovered_or_usable(self):
        college = self.submit_college(self.first_admin, 'Pending College')
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get('/api/v1/colleges/').data['data'], [])
        self.assertEqual(self.client.get(f'/api/v1/colleges/{college.public_id}/').status_code, 404)
        with self.assertRaises(Exception):
            CollegeService.request_membership(college=college, user=self.outsider)
        self.assertEqual(self.verify(college).status_code, 200)
        self.assertEqual(college.__class__.objects.get(pk=college.pk).verification_status, College.VerificationStatus.VERIFIED)

    def test_submission_evidence_duplicate_prevention_and_private_status_tracking(self):
        self.client.force_authenticate(self.first_admin)
        missing_evidence = self.client.post('/api/v1/colleges/', {
            'name': 'Evidence College', 'city': 'Bengaluru',
        }, format='json')
        self.assertEqual(missing_evidence.status_code, 400)
        invalid_evidence = self.client.post('/api/v1/colleges/', {
            'name': 'Evidence College', 'city': 'Bengaluru',
            'verification_evidence': 'not-a-url',
        }, format='json')
        self.assertEqual(invalid_evidence.status_code, 400)

        college = self.submit_college(self.first_admin, 'Status College')
        duplicate = self.client.post('/api/v1/colleges/', {
            'name': ' status college ', 'city': 'bengaluru',
            'verification_evidence': 'https://example.edu/evidence',
        }, format='json')
        self.assertEqual(duplicate.status_code, 400)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get('/api/v1/colleges/', {'mine': 'true'}).data['data'], [])
        self.assertEqual(self.client.get('/api/v1/colleges/', {'status': 'pending'}).status_code, 403)

        self.client.force_authenticate(self.admin)
        rejected = self.client.post(
            f'/api/v1/admin/colleges/{college.public_id}/verification/',
            {'approve': False, 'reason': 'Evidence needs an official source.'}, format='json',
        )
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.client.force_authenticate(self.first_admin)
        own = self.client.get('/api/v1/colleges/', {'mine': 'true'}).data['data']
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]['verification_status'], College.VerificationStatus.REJECTED)
        self.assertEqual(own[0]['rejection_reason'], 'Evidence needs an official source.')
        self.assertTrue(own[0]['is_my_submission'])
        self.assertTrue(Notification.objects.filter(
            user=self.first_admin, event_key=f'college-review:{college.pk}:rejected',
        ).exists())
        self.client.force_authenticate(self.admin)
        repeated_review = self.client.post(
            f'/api/v1/admin/colleges/{college.public_id}/verification/',
            {'approve': True}, format='json',
        )
        self.assertEqual(repeated_review.status_code, 400)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f'/api/v1/colleges/{college.public_id}/').status_code, 404)

    def test_only_college_admin_can_manage_roster_and_only_members_are_eligible(self):
        college = self.submit_college(self.first_admin, 'Roster College')
        self.verify(college)
        CollegeMembership.objects.filter(college=college, user=self.first_admin).update(status=CollegeMembership.Status.ACTIVE)
        other = self.submit_college(self.second_admin, 'Other College')
        self.verify(other)
        CollegeMembership.objects.filter(college=other, user=self.second_admin).update(status=CollegeMembership.Status.ACTIVE)
        challenge = CollegeChallenge.objects.create(challenger=college, challenged=other, sport=self.sport, created_by=self.first_admin, status=CollegeChallenge.Status.ACCEPTED)
        tournament = Tournament.objects.create(tournament_reference='TR-COLLEGE-ROSTER', name='College League', organizer=self.second_admin, sport=self.sport, venue=self.venue, start_date=timezone.localdate()+timedelta(days=3), end_date=timezone.localdate()+timedelta(days=4), registration_deadline=timezone.now()+timedelta(days=2), maximum_teams=2, format=Tournament.Format.ROUND_ROBIN, is_inter_college=True)
        challenge.tournament = tournament
        challenge.save(update_fields=['tournament'])
        self.client.force_authenticate(self.outsider)
        unauthorized = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/college-teams/', {'college_id': str(college.public_id), 'name': 'Team A'}, format='json')
        self.assertEqual(unauthorized.status_code, 403)
        self.client.force_authenticate(self.first_admin)
        first = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/college-teams/', {'college_id': str(college.public_id), 'name': 'Team A'}, format='json')
        self.client.force_authenticate(self.second_admin)
        second = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/college-teams/', {'college_id': str(other.public_id), 'name': 'Team B'}, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        team = TournamentTeam.objects.get(public_id=first.data['data']['public_id'])
        self.client.force_authenticate(self.first_admin)
        rejected = self.client.post(f'/api/v1/college-teams/{team.public_id}/roster/', {'user_id': self.outsider.id}, format='json')
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(self.client.get(f'/api/v1/college-teams/{team.public_id}/roster/').status_code, 200)
        self.activate_player(college, self.first_player)
        accepted = self.client.post(f'/api/v1/college-teams/{team.public_id}/roster/', {'user_id': self.first_player.id}, format='json')
        self.assertEqual(accepted.status_code, 201, accepted.data)
        roster = self.client.get(f'/api/v1/college-teams/{team.public_id}/roster/')
        self.assertIn(self.first_player.id, [item['user_id'] for item in roster.data['data']])
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f'/api/v1/college-teams/{team.public_id}/roster/').status_code, 403)
        self.client.force_authenticate(self.first_admin)
        duplicate_team = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/college-teams/', {'college_id': str(college.public_id), 'name': 'Another team'}, format='json')
        self.assertEqual(duplicate_team.status_code, 400)
        self.activate_player(other, self.first_player)
        self.client.force_authenticate(self.second_admin)
        second_team = TournamentTeam.objects.get(public_id=second.data['data']['public_id'])
        cross_team_duplicate = self.client.post(f'/api/v1/college-teams/{second_team.public_id}/roster/', {'user_id': self.first_player.id}, format='json')
        self.assertEqual(cross_team_duplicate.status_code, 400)
        Tournament.objects.filter(pk=tournament.pk).update(status=Tournament.Status.STARTED)
        closed_registration = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/college-teams/', {'college_id': str(college.public_id), 'name': 'Late team'}, format='json')
        self.assertEqual(closed_registration.status_code, 400)

    def test_challenge_fixture_result_dispute_and_standings_use_tournament_records(self):
        first = self.submit_college(self.first_admin, 'Fixture College A')
        second = self.submit_college(self.second_admin, 'Fixture College B')
        self.verify(first); self.verify(second)
        CollegeMembership.objects.filter(college=first, user=self.first_admin).update(status=CollegeMembership.Status.ACTIVE)
        CollegeMembership.objects.filter(college=second, user=self.second_admin).update(status=CollegeMembership.Status.ACTIVE)
        self.client.force_authenticate(self.first_admin)
        challenge_response = self.client.post(f'/api/v1/colleges/{first.public_id}/challenges/', {'challenged_college_id': str(second.public_id), 'sport_id': self.sport.id, 'message': 'Friendly fixture'}, format='json')
        self.assertEqual(challenge_response.status_code, 201, challenge_response.data)
        duplicate_challenge = self.client.post(f'/api/v1/colleges/{first.public_id}/challenges/', {'challenged_college_id': str(second.public_id), 'sport_id': self.sport.id}, format='json')
        self.assertEqual(duplicate_challenge.status_code, 400)
        challenge_id = challenge_response.data['data']['public_id']
        self.client.force_authenticate(self.second_admin)
        answer = self.client.post(f'/api/v1/college-challenges/{challenge_id}/answer/', {'accept': True, 'venue_id': str(self.venue.public_id), 'start_date': (timezone.localdate()+timedelta(days=7)).isoformat(), 'end_date': (timezone.localdate()+timedelta(days=7)).isoformat(), 'registration_deadline': (timezone.now()+timedelta(days=5)).isoformat()}, format='json')
        self.assertEqual(answer.status_code, 200, answer.data)
        tournament = Tournament.objects.get(public_id=answer.data['data']['tournament_id'])
        self.assertTrue(tournament.is_inter_college)
        self.client.force_authenticate(self.first_admin)
        ta = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/college-teams/', {'college_id': str(first.public_id), 'name': 'A'}, format='json')
        self.client.force_authenticate(self.second_admin)
        tb = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/college-teams/', {'college_id': str(second.public_id), 'name': 'B'}, format='json')
        self.assertEqual(ta.status_code, 201, ta.data); self.assertEqual(tb.status_code, 201, tb.data)
        match = TournamentMatch.objects.create(tournament=tournament, round_number=1, match_number=1, team_a=TournamentTeam.objects.get(public_id=ta.data['data']['public_id']), team_b=TournamentTeam.objects.get(public_id=tb.data['data']['public_id']))
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.post(f'/api/v1/tournaments/{tournament.public_id}/matches/{match.public_id}/college-result/', {'score_a': 2, 'score_b': 1}, format='json').status_code, 403)
        self.assertEqual(self.client.get(f'/api/v1/tournament-matches/{match.public_id}/college-disputes/').status_code, 403)
        self.client.force_authenticate(self.first_admin)
        result = self.client.post(f'/api/v1/tournaments/{tournament.public_id}/matches/{match.public_id}/college-result/', {'score_a': 2, 'score_b': 1}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        standings = TournamentStandingsService.standings(tournament)
        self.assertEqual([(r['team'].name, r['points']) for r in standings], [('A', 3), ('B', 0)])
        self.activate_player(first, self.first_player)
        TournamentTeamMember.objects.create(team=match.team_a, user=self.first_player)
        self.client.force_authenticate(self.first_player)
        dispute = self.client.post(f'/api/v1/tournament-matches/{match.public_id}/college-disputes/', {'reason': 'Score report was entered incorrectly.'}, format='json')
        self.assertEqual(dispute.status_code, 201, dispute.data)
        self.assertEqual(self.client.get(f'/api/v1/tournament-matches/{match.public_id}/college-disputes/').status_code, 200)
        self.client.force_authenticate(self.first_admin)
        self.assertEqual(self.client.get('/api/v1/admin/college-result-disputes/').status_code, 403)
        self.client.force_authenticate(self.admin)
        queue = self.client.get('/api/v1/admin/college-result-disputes/')
        self.assertEqual(queue.status_code, 200, queue.data)
        self.assertEqual(len(queue.data['data']), 1)
        self.assertEqual(queue.data['data'][0]['tournament_name'], tournament.name)
        resolved = self.client.post(f'/api/v1/admin/college-result-disputes/{dispute.data["data"]["public_id"]}/resolve/', {'resolution': 'Confirmed after reviewing the signed score sheet.'}, format='json')
        self.assertEqual(resolved.status_code, 200, resolved.data)
        self.assertEqual(CollegeResultDispute.objects.get(pk=match.college_disputes.first().pk).status, CollegeResultDispute.Status.RESOLVED)


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
        self.assertEqual(self.client.post(f'/api/v1/notifications/{self.notification.public_id}/read/').status_code,404)

    def test_notification_list_is_paginated_and_private_to_current_user(self):
        Notification.objects.create(user=self.other, type='message', title='Private')
        response = self.client.get('/api/v1/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['public_id'], str(self.notification.public_id))

    def test_notification_filters_reject_malformed_values(self):
        self.assertEqual(self.client.get('/api/v1/notifications/', {'is_read':'perhaps'}).status_code,400)
        self.assertEqual(self.client.get('/api/v1/notifications/', {'created_after':'not-a-date'}).status_code,400)

    def test_event_key_makes_creation_idempotent_and_preferences_apply(self):
        from my_app.services.notifications import NotificationService
        first=NotificationService.create_notification(user=self.user,type='booking_created',title='Booked',event_key='booking:123')
        second=NotificationService.create_notification(user=self.user,type='booking_created',title='Retry',event_key='booking:123')
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(Notification.objects.filter(user=self.user,event_key='booking:123').count(),1)
        NotificationPreference.objects.update_or_create(user=self.user,defaults={'booking_notifications':False})
        self.assertIsNone(NotificationService.create_notification(user=self.user,type='booking_created',title='Muted',event_key='booking:124'))

    def test_device_registration_is_private_and_does_not_return_token(self):
        response=self.client.post('/api/v1/notifications/devices/',{'device_token':'opaque-token','platform':'android'},format='json')
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.data['delivery_status'],'not_configured')
        self.assertNotIn('device_token',response.data['data'])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.delete(f"/api/v1/notifications/devices/{response.data['data']['public_id']}/").status_code,404)


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

    def test_dashboard_serializes_real_recommendation_results(self):
        sport = Sport.objects.get(name='Badminton')
        UserSport.objects.create(user=self.user, sport=sport, preferred=True)
        venue = Venue.objects.create(
            name='Recommendation Arena', address='1 Sports Road', city='Bengaluru',
            latitude=12.9716, longitude=77.5946, rating=4.5,
            opening_time='06:00', closing_time='22:00',
        )
        Court.objects.create(venue=venue, sport=sport, name='Court 1', price_per_hour=300)
        community = Community.objects.create(
            name='Badminton Players', slug='recommendation-badminton-players',
            owner=self.user, sport=sport, city='Bengaluru',
        )
        now = timezone.now()
        local_start = (now + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(hours=1)
        Event.objects.create(
            event_reference='REC-EVENT-1', title='Community Badminton', organizer=self.user,
            sport=sport, venue=venue, event_date=local_start.date(), start_time=local_start.time(),
            end_time=local_end.time(), starts_at=local_start, ends_at=local_end,
            maximum_participants=12, minimum_participants=1, registration_deadline=now + timedelta(days=1),
        )
        Tournament.objects.create(
            tournament_reference='REC-TOURNAMENT-1', name='Bengaluru Open', organizer=self.user,
            sport=sport, venue=venue, start_date=local_start.date(), end_date=local_end.date(),
            registration_deadline=now + timedelta(days=1), maximum_teams=8,
        )

        response = self.client.get('/api/v1/recommendations/')

        self.assertEqual(response.status_code, 200, response.data)
        data = response.data['data']
        self.assertEqual([item['id'] for item in data['venues']], [str(venue.public_id)])
        self.assertEqual([item['id'] for item in data['communities']], [str(community.public_id)])
        self.assertEqual(len(data['events']), 1)
        self.assertEqual(data['events'][0]['metadata']['venue'], venue.name)
        self.assertEqual(len(data['tournaments']), 1)
        self.assertEqual(data['tournaments'][0]['metadata']['venue'], venue.name)




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

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.utils import timezone
from django.core.cache import cache
from datetime import timedelta
from unittest.mock import patch
from rest_framework.test import APITestCase
from rest_framework.exceptions import ValidationError

from .models import Ground, SportsGround, Match, MatchReplacementOpportunity, MatchReplacementInvitation, MatchReplacementHistory, PracticeActivity, PracticeCompletion, SportsGoal, HealthActivityRecord, NotificationPreference, PlayerProfile, Sport, AvailabilitySlot, UserSport, UserBlock, Venue, VenueSport, Court, VenueAmenity, CourtBooking, VenueReview, CourtBlockedPeriod, Game, GamePlayer, Conversation, ConversationMember, Message, Community, CommunityMember, CommunityPostLike, Event, EventParticipant, EventReminder, Tournament, Notification, UserDevice, RecommendationProfile, Report, ModerationAction, PlayerRating, MatchReflection, ReferralAttribution, College, CollegeMembership, CollegeChallenge, TournamentTeam, TournamentTeamMember, TournamentMatch, CollegeResultDispute, MatchTimePoll, MatchTimePollOption, MatchTimePollVote, Achievement, UserAchievement
from .services.matches import accept_rescue_invitation
from .services.locations import nearby_queryset
from .services.ground_ingestion import GroundIngestionService
from .services.player_matching import PlayerMatchingService
from .services.availability import VenueAvailabilityService
from .services.bookings import BookingStatusService
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

User = get_user_model()


class HealthActivityApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='health-user', email='health@example.com', password='pw123456')
        self.other = User.objects.create_user(username='health-other', email='health-other@example.com', password='pw123456')
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()

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

    def test_import_is_idempotent_and_records_are_private(self):
        record = {'metric': 'steps', 'value': 7000, 'recorded_date': self.today.isoformat(), 'external_record_id': 'hc-record-1'}
        payload = {'source': 'health_connect', 'records': [record, record]}
        first = self.client.post('/api/v1/health/activity/sync/', payload, format='json')
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data['data'], {'created': 1, 'updated': 1})
        self.assertEqual(HealthActivityRecord.objects.filter(user=self.user).count(), 1)
        self.client.force_authenticate(self.other)
        other_summary = self.client.get(f'/api/v1/health/activity/?start={self.today.isoformat()}&end={self.today.isoformat()}')
        self.assertEqual(other_summary.data['data']['records'], [])
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
        self.assertTrue(MatchReplacementHistory.objects.filter(match=match, replacement_user=candidate).exists())
        self.assertEqual(MatchReplacementOpportunity.objects.get(pk=invitation.opportunity_id).closed_at is None, False)

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
        Match.objects.create(
            sport=self.sport, ground=self.ground, organizer=self.organizer,
            date_time=timezone.now() + timedelta(days=1), total_players=2,
        )
        self.client.force_authenticate(self.player)
        response = self.client.get('/api/v1/ai/matches/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_ai_recommendations_exclude_sports_not_in_player_profile(self):
        cricket = Sport.objects.get(name='Cricket')
        UserSport.objects.create(user=self.player, sport=self.sport, skill_level='intermediate', preferred=True)
        Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(days=1), total_players=2)
        Match.objects.create(sport=cricket, ground=self.ground, organizer=self.organizer, date_time=timezone.now() + timedelta(days=1), total_players=2)
        self.client.force_authenticate(self.player)
        response = self.client.get('/api/v1/ai/matches/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['sportName'], self.sport.name)


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

    def test_home_player_feed_uses_database_distance_filter_and_bounded_results(self):
        response = self.client.get('/api/v1/players/', {
            'lat': 12.9716, 'lng': 77.5946, 'radius': 2, 'sport': 'badminton',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['username'] for row in response.data], ['near-player'])
        self.assertLess(response.data[0]['distance_km'], 1)
        self.assertEqual(response.data[0]['rating_summary']['rating_count'], 0)

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
        self.assertEqual(recommendations.data[0]['public_id'], first.data['data']['public_id'])


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

    def test_recommendations_are_actual_and_save_mood(self):
        response = self.client.get('/api/v1/recommendations/match-my-mood/', {'sport': self.sport.id, 'mood': 'relaxed', 'radius': 20, 'availability': 'evening'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['mood'], 'relaxed')
        self.assertTrue(response.data['games'])
        self.assertTrue(response.data['players'])
        self.assertEqual(response.data['games'][0]['game']['public_id'], str(self.game.public_id))
        self.assertEqual(self.user.profile.matching_mood, 'relaxed')
        self.assertIn('ranking_formula', response.data)
        self.assertIn('Your play moods are compatible', response.data['players'][0]['reasons'])

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


class VenueOwnerOperationsTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='venue-ops-owner', email='venue-ops@example.com', password='StrongPass!123')
        owner_group, _ = Group.objects.get_or_create(name='SportMate Venue Owners')
        self.owner.groups.add(owner_group)
        self.other_owner = User.objects.create_user(username='other-venue-owner', email='other-venue@example.com', password='StrongPass!123')
        self.other_owner.groups.add(owner_group)
        self.admin = User.objects.create_superuser(username='venue-superowner', email='venue-admin@example.com', password='StrongPass!123')
        self.sport = Sport.objects.get(name='Badminton')
        self.venue = Venue.objects.create(
            owner=self.owner, name='Owner Operations Arena', address='1 Main Road', city='Bengaluru',
            latitude=12.9716, longitude=77.5946, opening_time='06:00', closing_time='22:00', active=True,
        )
        VenueSport.objects.create(venue=self.venue, sport=self.sport)
        self.court = Court.objects.create(venue=self.venue, sport=self.sport, name='Court O1', capacity=4, price_per_hour=400)

    def test_owner_can_create_pending_venue_and_manage_own_courts(self):
        self.client.force_authenticate(self.owner)
        created = self.client.post('/api/v1/venue-owner/venues/', {
            'name': 'New Owner Ground', 'address': '12 Court Road', 'city': 'Mysuru',
            'latitude': 12.3, 'longitude': 76.6, 'opening_time': '06:00', 'closing_time': '22:00',
            'timezone': 'Asia/Kolkata', 'sport_ids': [self.sport.id],
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertFalse(Venue.objects.get(public_id=created.data['data']['public_id']).active)
        court = self.client.post(f"/api/v1/venue-owner/venues/{self.venue.public_id}/courts/", {
            'name': 'Court O2', 'sport_id': self.sport.id, 'capacity': 6, 'price_per_hour': '500.00',
        }, format='json')
        self.assertEqual(court.status_code, 201, court.data)
        deactivated = self.client.delete(f"/api/v1/venue-owner/courts/{court.data['data']['public_id']}/")
        self.assertEqual(deactivated.status_code, 200)
        self.assertFalse(Court.objects.get(public_id=court.data['data']['public_id']).active)

    def test_owner_is_scoped_and_cannot_approve_venue(self):
        self.client.force_authenticate(self.other_owner)
        self.assertEqual(self.client.get('/api/v1/venue-owner/venues/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/').status_code, 404)
        pending = Venue.objects.create(
            owner=self.other_owner, name='Pending Owner Ground', address='12 Court Road', city='Bengaluru',
            latitude=12.3, longitude=76.6, opening_time='06:00', closing_time='22:00', active=False,
        )
        response = self.client.patch(f'/api/v1/venue-owner/venues/{pending.public_id}/', {'active': True}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_superowner_can_approve_and_transfer_only_to_approved_owner(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f'/api/v1/admin/venues/{self.venue.public_id}/', {
            'active': False, 'owner_id': str(self.other_owner.public_id), 'reason': 'Operator reassignment',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.venue.refresh_from_db()
        self.assertFalse(self.venue.active)
        self.assertEqual(self.venue.owner_id, self.other_owner.id)
        pending = User.objects.create_user(username='not-approved-owner', email='not-approved@example.com', password='StrongPass!123')
        denied = self.client.patch(f'/api/v1/admin/venues/{self.venue.public_id}/', {'owner_id': str(pending.public_id)}, format='json')
        self.assertEqual(denied.status_code, 400)

    def test_owner_can_read_bookings_and_manage_future_maintenance_periods(self):
        self.client.force_authenticate(self.owner)
        now = timezone.now()
        booking = CourtBooking.objects.create(
            user=self.other_owner, court=self.court, venue=self.venue,
            booking_date=timezone.localdate() + timedelta(days=2),
            starts_at=now + timedelta(days=2), ends_at=now + timedelta(days=2, hours=1),
            status=CourtBooking.Status.CONFIRMED,
        )
        bookings = self.client.get('/api/v1/venue-owner/bookings/')
        self.assertEqual(bookings.status_code, 200)
        self.assertEqual(bookings.data['data'][0]['public_id'], str(booking.public_id))

        starts = now + timedelta(days=3)
        ends = starts + timedelta(hours=1)
        created = self.client.post(
            f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/{self.court.public_id}/blocked-periods/',
            {'starts_at': starts.isoformat(), 'ends_at': ends.isoformat(), 'type': 'maintenance', 'reason': 'Court resurfacing'},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        listed = self.client.get(f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/{self.court.public_id}/blocked-periods/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data['data']), 1)
        removed = self.client.delete(f"/api/v1/venue-owner/blocked-periods/{created.data['data']['public_id']}/")
        self.assertEqual(removed.status_code, 204)

    def test_non_owner_cannot_read_owner_dashboard(self):
        self.client.force_authenticate(User.objects.create_user(username='regular-player', password='StrongPass!123'))
        self.assertEqual(self.client.get('/api/v1/venue-owner/dashboard/').status_code, 403)


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
        created = self.create_poll()
        poll_id, options = created.data['data']['public_id'], created.data['data']['options']
        self.client.force_authenticate(self.player)
        self.client.post(f'/api/v1/match-time-polls/{poll_id}/vote/', {'option_id': options[0]['public_id']}, format='json')
        conflicting_start = options[0]['starts_at']
        Match.objects.create(sport=self.sport, ground=self.ground, organizer=self.other, date_time=conflicting_start, end_date_time=conflicting_start+timedelta(hours=2), total_players=2)
        self.client.force_authenticate(self.organizer)
        conflict = self.client.post(f'/api/v1/match-time-polls/{poll_id}/finalize/', {'option_id': options[0]['public_id']}, format='json')
        self.assertEqual(conflict.status_code, 400)
        self.assertEqual(MatchTimePoll.objects.get(public_id=poll_id).status, MatchTimePoll.Status.OPEN)


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
        self.assertEqual(detail.status_code, 403)
        self.assertFalse(Community.objects.filter(is_club=True, members__user=self.member, members__status=CommunityMember.Status.ACTIVE).exists())
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
        self.client.force_authenticate(self.organizer)
        changed = self.client.patch(f'/api/v1/events/{event.public_id}/', {'title': 'Updated event', 'description': 'New details'}, format='json')
        self.assertEqual(changed.status_code, 200, changed.data)
        event.refresh_from_db()
        self.assertEqual(event.title, 'Updated event')
        cancelled = self.client.delete(f'/api/v1/events/{event.public_id}/')
        self.assertEqual(cancelled.status_code, 204)
        event.refresh_from_db()
        self.assertEqual(event.status, Event.Status.CANCELLED)

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
        self.assertEqual(tournament.venue.source_nearby_ground, nearby)

    def test_tournament_registration_uses_team_records_and_enforces_deadline_capacity(self):
        start=timezone.localdate()+timedelta(days=5)
        tournament=Tournament.objects.create(tournament_reference='TR-REG',name='Open Tournament',organizer=self.organizer,sport=self.sport,venue=self.venue,start_date=start,end_date=start+timedelta(days=1),registration_deadline=timezone.now()+timedelta(days=3),maximum_teams=2)
        self.client.force_authenticate(self.first)
        joined=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/register/',{'team_name':'First Team'},format='json')
        self.assertEqual(joined.status_code,201,joined.data)
        self.assertEqual(TournamentTeamMember.objects.filter(team__tournament=tournament,user=self.first,status='active').count(),1)
        self.client.force_authenticate(self.second)
        self.assertEqual(self.client.post(f'/api/v1/tournaments/{tournament.public_id}/register/',{'team_name':'Second Team'},format='json').status_code,201)
        third=User.objects.create_user(username='event-third',email='event-third@example.com',password='StrongPass!123')
        self.client.force_authenticate(third)
        full=self.client.post(f'/api/v1/tournaments/{tournament.public_id}/register/',{'team_name':'Third Team'},format='json')
        self.assertEqual(full.status_code,400)

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
        self.activate_player(college, self.first_player)
        accepted = self.client.post(f'/api/v1/college-teams/{team.public_id}/roster/', {'user_id': self.first_player.id}, format='json')
        self.assertEqual(accepted.status_code, 201, accepted.data)

    def test_challenge_fixture_result_dispute_and_standings_use_tournament_records(self):
        first = self.submit_college(self.first_admin, 'Fixture College A')
        second = self.submit_college(self.second_admin, 'Fixture College B')
        self.verify(first); self.verify(second)
        CollegeMembership.objects.filter(college=first, user=self.first_admin).update(status=CollegeMembership.Status.ACTIVE)
        CollegeMembership.objects.filter(college=second, user=self.second_admin).update(status=CollegeMembership.Status.ACTIVE)
        self.client.force_authenticate(self.first_admin)
        challenge_response = self.client.post(f'/api/v1/colleges/{first.public_id}/challenges/', {'challenged_college_id': str(second.public_id), 'sport_id': self.sport.id, 'message': 'Friendly fixture'}, format='json')
        self.assertEqual(challenge_response.status_code, 201, challenge_response.data)
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
        self.client.force_authenticate(self.admin)
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

    def test_notification_list_is_paginated_and_private_to_current_user(self):
        Notification.objects.create(user=self.other, type='message', title='Private')
        response = self.client.get('/api/v1/notifications/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['public_id'], str(self.notification.public_id))


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

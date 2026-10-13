"""Challenge Arena service; scores are derived from existing verified records."""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from my_app.models import (
    Achievement, ChallengeEntry, ChallengeEntryMember, Community, CommunityMember, Game, GamePlayer,
    PracticeCompletion, SportChallenge, SportsGoal, TournamentMatch, UserAchievement, UserSport,
)
from my_app.services.communities import CommunityMembershipService
from my_app.services.goals import _source_count

User = get_user_model()
SKILL_ORDER = {value: index for index, value in enumerate(UserSport.SkillLevel.values)}


def _eligible_users(challenge, user_ids=None):
    users = User.objects.filter(is_active=True, account_status=User.AccountStatus.ACTIVE)
    if user_ids is not None:
        users = users.filter(pk__in=user_ids)
    prefs = UserSport.objects.filter(user_id__in=users.values('pk'))
    if challenge.sport_id:
        prefs = prefs.filter(sport_id=challenge.sport_id)
    if challenge.eligibility_min_skill:
        allowed = [skill for skill, rank in SKILL_ORDER.items() if rank >= SKILL_ORDER[challenge.eligibility_min_skill]]
        prefs = prefs.filter(skill_level__in=allowed)
    if challenge.sport_id or challenge.eligibility_min_skill:
        users = users.filter(pk__in=prefs.values('user_id'))
    return users


def _user_progress(challenge, user):
    if challenge.metric == SportChallenge.Metric.CALCULATED_GOALS_COMPLETED:
        goals = SportsGoal.objects.filter(
            user=user, created_at__date__lt=challenge.starts_on,
            start_date__gte=challenge.starts_on, end_date__lte=challenge.ends_on,
        ).exclude(progress_source=SportsGoal.Source.MANUAL)
        return sum(1 for goal in goals if _source_count(goal) >= goal.target)
    if challenge.metric == SportChallenge.Metric.TOURNAMENT_WINS:
        matches = TournamentMatch.objects.filter(
            status=TournamentMatch.Status.COMPLETED,
            winner__members__user=user,
            winner__members__status='active',
            tournament__start_date__lte=challenge.ends_on,
            tournament__end_date__gte=challenge.starts_on,
        )
        if challenge.sport_id:
            matches = matches.filter(tournament__sport_id=challenge.sport_id)
        return matches.distinct().count()
    if challenge.metric == SportChallenge.Metric.CHECKLIST_COMPLETIONS:
        return PracticeCompletion.objects.filter(user=user, practice_date__range=(challenge.starts_on, challenge.ends_on)).count()
    if challenge.metric == SportChallenge.Metric.PRACTICE_DAYS:
        return PracticeCompletion.objects.filter(user=user, practice_date__range=(challenge.starts_on, challenge.ends_on)).values('practice_date').distinct().count()
    games = Game.objects.filter(
        status=Game.Status.COMPLETED,
        game_date__range=(challenge.starts_on, challenge.ends_on),
        game_players__user=user,
        game_players__status=GamePlayer.Status.CONFIRMED,
    )
    if challenge.sport_id:
        games = games.filter(sport_id=challenge.sport_id)
    return games.distinct().count()


def _entry_progress(entry):
    challenge = entry.challenge
    if entry.user_id:
        if _eligible_users(challenge, [entry.user_id]).exists():
            return _user_progress(challenge, entry.user)
        return 0
    eligible = list(entry.roster.values_list('user_id', flat=True))
    if not eligible:
        return 0
    if challenge.metric == SportChallenge.Metric.CALCULATED_GOALS_COMPLETED:
        goals = SportsGoal.objects.filter(
            user_id__in=eligible, created_at__date__lt=challenge.starts_on,
            start_date__gte=challenge.starts_on, end_date__lte=challenge.ends_on,
        ).exclude(progress_source=SportsGoal.Source.MANUAL)
        return sum(1 for goal in goals if _source_count(goal) >= goal.target)
    if challenge.metric == SportChallenge.Metric.TOURNAMENT_WINS:
        matches = TournamentMatch.objects.filter(
            status=TournamentMatch.Status.COMPLETED,
            winner__members__user_id__in=eligible,
            winner__members__status='active',
            tournament__start_date__lte=challenge.ends_on,
            tournament__end_date__gte=challenge.starts_on,
        )
        if challenge.sport_id:
            matches = matches.filter(tournament__sport_id=challenge.sport_id)
        return matches.values('id', 'winner__members__user_id').distinct().count()
    if challenge.metric == SportChallenge.Metric.CHECKLIST_COMPLETIONS:
        return PracticeCompletion.objects.filter(user_id__in=eligible, practice_date__range=(challenge.starts_on, challenge.ends_on)).count()
    if challenge.metric == SportChallenge.Metric.PRACTICE_DAYS:
        return PracticeCompletion.objects.filter(user_id__in=eligible, practice_date__range=(challenge.starts_on, challenge.ends_on)).values('user_id', 'practice_date').distinct().count()
    games = Game.objects.filter(
        status=Game.Status.COMPLETED,
        game_date__range=(challenge.starts_on, challenge.ends_on),
        game_players__user_id__in=eligible,
        game_players__status=GamePlayer.Status.CONFIRMED,
    )
    if challenge.sport_id:
        games = games.filter(sport_id=challenge.sport_id)
    return games.values('id', 'game_players__user_id').distinct().count()


@transaction.atomic
def _award_badge(entry, progress):
    challenge = entry.challenge
    if not challenge.reward_achievement_id or progress < challenge.target:
        return
    if entry.user_id:
        recipients = [entry.user]
    else:
        recipients = User.objects.filter(pk__in=entry.roster.values_list('user_id', flat=True))
    for user in recipients:
        badge, _ = UserAchievement.objects.get_or_create(user=user, achievement=challenge.reward_achievement)
        if not badge.unlocked:
            badge.progress = max(badge.progress, challenge.target)
            badge.unlocked = True
            badge.unlocked_at = timezone.now()
            badge.save(update_fields=['progress', 'unlocked', 'unlocked_at', 'updated_at'])


def serialize_entry(entry, *, include_members=False):
    progress = _entry_progress(entry)
    _award_badge(entry, progress)
    challenge = entry.challenge
    if entry.user_id:
        profile = getattr(entry.user, 'profile', None)
        user_name = entry.user.username if not profile or profile.profile_visibility == 'public' else f"Player {entry.user.public_id.hex[:6]}"
        participant = {'public_id': str(entry.user.public_id), 'name': user_name}
    else:
        team_name = entry.team.name if entry.team.visibility == Community.Visibility.PUBLIC else 'Private club team'
        participant = {'public_id': str(entry.team.public_id), 'name': team_name}
    payload = {
        'public_id': str(entry.public_id),
        'participant_type': 'individual' if entry.user_id else 'team',
        'participant': participant,
        'progress': progress,
        'target': challenge.target,
        'percentage': min(100, round(progress / challenge.target * 100)),
        # Points are a derived reward, never accepted from a client.
        'points': challenge.reward_points if progress >= challenge.target else 0,
        'status': 'completed' if progress >= challenge.target else ('left' if entry.left_at else 'active'),
        'joined_at': entry.joined_at.isoformat(),
    }
    if include_members and entry.team_id:
        payload['member_count'] = entry.roster.count()
    return payload


def serialize_challenge(challenge, *, user=None):
    today = timezone.localdate()
    if not challenge.is_active:
        status = 'inactive'
    elif today < challenge.starts_on:
        status = 'upcoming'
    elif today > challenge.ends_on:
        status = 'completed'
    else:
        status = 'active'
    entries = list(challenge.entries.filter(left_at__isnull=True).select_related('user', 'team', 'challenge__reward_achievement'))
    my_entries = []
    if user:
        for entry in entries:
            if entry.user_id == user.id:
                payload = serialize_entry(entry, include_members=True)
                payload['can_leave'] = True
                my_entries.append(payload)
            elif entry.team_id and entry.roster.filter(user=user).exists():
                payload = serialize_entry(entry, include_members=True)
                role = CommunityMember.objects.filter(community=entry.team, user=user, status=CommunityMember.Status.ACTIVE).values_list('role', flat=True).first()
                payload['can_leave'] = role in (CommunityMember.Role.OWNER, CommunityMember.Role.CAPTAIN, CommunityMember.Role.ADMIN)
                my_entries.append(payload)
    data = {
        'public_id': str(challenge.public_id), 'name': challenge.name, 'description': challenge.description,
        'sport': {'id': challenge.sport_id, 'name': challenge.sport.name} if challenge.sport_id else None,
        'kind': challenge.kind, 'metric': challenge.metric, 'metric_label': challenge.get_metric_display(),
        'target': challenge.target, 'eligibility_min_skill': challenge.eligibility_min_skill,
        'starts_on': challenge.starts_on.isoformat(), 'ends_on': challenge.ends_on.isoformat(),
        'status': status, 'reward_points': challenge.reward_points,
        'reward_badge': challenge.reward_achievement.name if challenge.reward_achievement_id else None,
        'tie_rule': 'Equal progress shares the same rank; next rank skips the tied positions.',
        'progress_rule': 'Progress is recalculated from completed games or saved Daily Practice records. Corrections to those records immediately affect progress and points; a badge already unlocked is historical.',
        'entry_count': len(entries),
        'my_entries': my_entries,
    }
    return data


class ChallengeService:
    @staticmethod
    @transaction.atomic
    def create(*, actor, data):
        values = dict(data)
        sport_id = values.pop('sport_id', None)
        achievement_public_id = values.pop('reward_achievement_id', None)
        achievement = Achievement.objects.get(public_id=achievement_public_id) if achievement_public_id else None
        return SportChallenge.objects.create(
            **values, sport_id=sport_id, reward_achievement=achievement, created_by=actor,
        )

    @staticmethod
    def get(*, public_id):
        try:
            return SportChallenge.objects.select_related('sport', 'reward_achievement').get(public_id=public_id)
        except SportChallenge.DoesNotExist as exc:
            raise NotFound('Challenge not found.') from exc

    @classmethod
    @transaction.atomic
    def join(cls, *, challenge, user, team_public_id=None):
        challenge = SportChallenge.objects.select_for_update().get(pk=challenge.pk)
        today = timezone.localdate()
        if not challenge.is_active or today > challenge.ends_on:
            raise ValidationError({'challenge': 'This challenge is no longer accepting entries.'})
        if challenge.kind == SportChallenge.Kind.INDIVIDUAL:
            if team_public_id:
                raise ValidationError({'team_public_id': 'This is an individual challenge.'})
            if not _eligible_users(challenge, [user.pk]).exists():
                raise PermissionDenied('Your saved sport and skill profile does not meet this challenge eligibility rule.')
            entry_filter = {'user': user}
        else:
            if not team_public_id:
                raise ValidationError({'team_public_id': 'Choose a club team for this challenge.'})
            team = get_object_or_404(Community, public_id=team_public_id, is_club=True, is_active=True)
            CommunityMembershipService.admin(community=team, user=user)
            if challenge.sport_id and team.sport_id != challenge.sport_id:
                raise ValidationError({'team_public_id': 'The club sport does not match this challenge.'})
            eligible = _eligible_users(challenge, CommunityMember.objects.filter(community=team, status=CommunityMember.Status.ACTIVE).values_list('user_id', flat=True))
            if not eligible.exists():
                raise ValidationError({'team_public_id': 'The club has no active members eligible for this challenge.'})
            entry_filter = {'team': team}
        try:
            with transaction.atomic():
                entry, created = ChallengeEntry.objects.get_or_create(
                    challenge=challenge, **entry_filter,
                    defaults={'user': user if challenge.kind == SportChallenge.Kind.INDIVIDUAL else None},
                )
        except IntegrityError:
            entry = ChallengeEntry.objects.get(challenge=challenge, **entry_filter)
            created = False
        if not created and entry.left_at:
            entry.left_at = None
            entry.save(update_fields=['left_at', 'updated_at'])
        if challenge.kind == SportChallenge.Kind.TEAM and created:
            ChallengeEntryMember.objects.bulk_create(
                [ChallengeEntryMember(entry=entry, user_id=member_id) for member_id in eligible.values_list('pk', flat=True)],
                ignore_conflicts=True,
            )
        return entry, created

    @staticmethod
    @transaction.atomic
    def leave(*, challenge, user, team_public_id=None):
        if challenge.kind == SportChallenge.Kind.TEAM:
            if not team_public_id:
                raise ValidationError({'team_public_id': 'Choose a club team.'})
            team = get_object_or_404(Community, public_id=team_public_id, is_club=True)
            CommunityMembershipService.admin(community=team, user=user)
            entry = get_object_or_404(ChallengeEntry.objects.select_for_update(), challenge=challenge, team=team, left_at__isnull=True)
        else:
            entry = get_object_or_404(ChallengeEntry.objects.select_for_update(), challenge=challenge, user=user, left_at__isnull=True)
        if timezone.localdate() > challenge.ends_on:
            raise ValidationError({'challenge': 'Completed challenge entries are historical and cannot be changed.'})
        entry.left_at = timezone.now()
        entry.save(update_fields=['left_at', 'updated_at'])
        return entry

    @classmethod
    def leaderboard(cls, *, challenge, limit=100):
        entries = challenge.entries.filter(left_at__isnull=True).select_related('user', 'team', 'challenge__reward_achievement').order_by('joined_at')
        rows = [serialize_entry(entry, include_members=True) for entry in entries]
        rows.sort(key=lambda row: (-row['progress'], -row['points'], row['joined_at'], row['participant']['name'].casefold()))
        previous = None
        rank = 0
        for index, row in enumerate(rows, start=1):
            key = (row['progress'], row['points'])
            if key != previous:
                rank = index
                previous = key
            row['rank'] = rank
        return rows[:limit]

    @classmethod
    def list_for(cls, *, user, status=None):
        qs = SportChallenge.objects.filter(is_active=True).select_related('sport', 'reward_achievement').order_by('starts_on', 'name')
        if status == 'inactive':
            qs = SportChallenge.objects.filter(is_active=False).select_related('sport', 'reward_achievement').order_by('-updated_at', 'name')
        if status in ('active', 'upcoming', 'completed'):
            today = timezone.localdate()
            if status == 'active': qs = qs.filter(starts_on__lte=today, ends_on__gte=today)
            elif status == 'upcoming': qs = qs.filter(starts_on__gt=today)
            else: qs = qs.filter(ends_on__lt=today)
        return [serialize_challenge(item, user=user) for item in qs[:100]]

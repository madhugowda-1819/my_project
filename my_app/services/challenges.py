"""Server-calculated player challenge participation and rewards."""
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from my_app.models import (
    Achievement, ChallengeEntry, ChallengeEntryMember, ChallengeRewardGrant,
    Community, CommunityMember, Game, PracticeCompletion, SportsGoal,
    SportChallenge, TournamentMatch, TournamentTeamMember, UserAchievement,
    UserSport,
)
from my_app.services.notifications import NotificationService


SKILL_RANK = {
    UserSport.SkillLevel.BEGINNER: 1,
    UserSport.SkillLevel.INTERMEDIATE: 2,
    UserSport.SkillLevel.ADVANCED: 3,
    UserSport.SkillLevel.EXPERT: 4,
    UserSport.SkillLevel.PRO: 5,
}


class ChallengeService:
    """Challenge definitions are platform-managed; players can join and leave."""

    @staticmethod
    def _eligible(user, challenge):
        if not user.is_active or user.account_status != user.AccountStatus.ACTIVE:
            raise PermissionDenied('An active player account is required.')
        if not hasattr(user, 'profile'):
            raise PermissionDenied('Challenges are available to player accounts only.')
        if challenge.sport_id and not UserSport.objects.filter(user=user, sport_id=challenge.sport_id).exists():
            raise ValidationError({'sport': ['Add this sport to your player profile before joining.']})
        if challenge.eligibility_min_skill:
            skill = UserSport.objects.filter(
                user=user, sport_id=challenge.sport_id,
            ).values_list('skill_level', flat=True).first()
            if SKILL_RANK.get(skill, 0) < SKILL_RANK.get(challenge.eligibility_min_skill, 99):
                raise ValidationError({'eligibility': ['Your recorded skill level does not meet this challenge.']})

    @staticmethod
    def _window(challenge):
        today = timezone.localdate()
        return challenge.starts_on, min(challenge.ends_on, today)

    @classmethod
    def metric(cls, *, user, challenge):
        start, end = cls._window(challenge)
        if end < start:
            return 0
        metric = challenge.metric
        if metric == SportChallenge.Metric.MATCHES_COMPLETED:
            games = Game.objects.filter(
                status=Game.Status.COMPLETED,
                game_date__range=(start, end),
                game_players__user=user,
            ).filter(
                Q(game_players__left_at__isnull=True)
                | Q(game_players__left_at__gt=F('starts_at'))
            )
            if challenge.sport_id:
                games = games.filter(sport_id=challenge.sport_id)
            return games.distinct().count()
        if metric in (SportChallenge.Metric.PRACTICE_DAYS,
                      SportChallenge.Metric.CHECKLIST_COMPLETIONS):
            completions = PracticeCompletion.objects.filter(
                user=user, practice_date__range=(start, end),
            )
            if metric == SportChallenge.Metric.PRACTICE_DAYS:
                return completions.values('practice_date').distinct().count()
            return completions.count()
        if metric == SportChallenge.Metric.TOURNAMENT_WINS:
            wins = TournamentMatch.objects.filter(
                status=TournamentMatch.Status.COMPLETED,
                winner__members__user=user,
                tournament__start_date__lte=end,
                tournament__end_date__gte=start,
            )
            if challenge.sport_id:
                wins = wins.filter(tournament__sport_id=challenge.sport_id)
            return wins.distinct().count()
        if metric == SportChallenge.Metric.CALCULATED_GOALS_COMPLETED:
            goals = SportsGoal.objects.filter(
                user=user, status=SportsGoal.Status.COMPLETED,
                start_date__lte=end, end_date__gte=start,
                progress_source__in=(SportsGoal.Source.PRACTICE_DAYS,
                                    SportsGoal.Source.MATCHES_COMPLETED,
                                    SportsGoal.Source.CHECKLIST_COMPLETIONS),
            )
            return goals.count()
        return 0

    @classmethod
    def entry_progress(cls, *, entry):
        if entry.user_id:
            return cls.metric(user=entry.user, challenge=entry.challenge)
        return sum(
            cls.metric(user=member.user, challenge=entry.challenge)
            for member in entry.roster.select_related('user').all()
        )

    @classmethod
    @transaction.atomic
    def _grant_rewards(cls, *, entry, progress):
        challenge = entry.challenge
        if not challenge.is_active or progress < challenge.target:
            return
        recipients = ([entry.user] if entry.user_id else
                      [member.user for member in entry.roster.select_related('user').all()])
        for user in recipients:
            # A database uniqueness constraint makes retried reads/actions safe.
            try:
                grant, created = ChallengeRewardGrant.objects.get_or_create(
                    entry=entry, user=user,
                    defaults={'points': challenge.reward_points},
                )
            except IntegrityError:
                grant = ChallengeRewardGrant.objects.get(entry=entry, user=user)
                created = False
            if not created:
                continue
            if challenge.reward_achievement_id:
                achievement = challenge.reward_achievement
                user_achievement, _ = UserAchievement.objects.get_or_create(
                    user=user, achievement=achievement,
                    defaults={'progress': achievement.target_value, 'unlocked': True,
                              'unlocked_at': timezone.now()},
                )
                if not user_achievement.unlocked:
                    user_achievement.progress = max(user_achievement.progress, achievement.target_value)
                    user_achievement.unlocked = True
                    user_achievement.unlocked_at = timezone.now()
                    user_achievement.save(update_fields=['progress', 'unlocked', 'unlocked_at', 'updated_at'])
            NotificationService.create_notification(
                user=user, type='reminder', title='Challenge completed',
                body=f'You completed {challenge.name}.',
                data={'challenge_id': challenge.public_id.hex, 'points': grant.points},
                event_key=f'challenge-reward:{entry.public_id}:{user.pk}',
            )

    @classmethod
    @transaction.atomic
    def join(cls, *, challenge, user, team_id=None):
        challenge = SportChallenge.objects.select_for_update().get(pk=challenge.pk)
        if not challenge.is_active or challenge.ends_on < timezone.localdate():
            raise ValidationError({'challenge': ['This challenge is inactive or has ended.']})
        if challenge.kind == SportChallenge.Kind.INDIVIDUAL:
            if team_id:
                raise ValidationError({'team_id': ['This is an individual challenge.']})
            cls._eligible(user, challenge)
            lookup = {'user': user, 'team': None}
            members = [user]
        else:
            if not team_id:
                raise ValidationError({'team_id': ['Select a team to join this challenge.']})
            team = Community.objects.filter(public_id=team_id, is_active=True, is_club=True).first()
            if team is None:
                raise NotFound('Team not found.')
            membership = CommunityMember.objects.filter(
                community=team, user=user, status=CommunityMember.Status.ACTIVE,
                role__in=(CommunityMember.Role.OWNER, CommunityMember.Role.CAPTAIN,
                          CommunityMember.Role.ADMIN),
            ).exists()
            if not membership:
                raise PermissionDenied('Only an active team owner, captain or admin can enter it.')
            members = list(team.members.filter(status=CommunityMember.Status.ACTIVE).select_related('user'))
            if not members:
                raise ValidationError({'team_id': ['The team has no active members.']})
            for membership in members:
                cls._eligible(membership.user, challenge)
            lookup = {'user': None, 'team': team}
        entry = ChallengeEntry.objects.filter(challenge=challenge, **lookup).first()
        if entry:
            if entry.left_at is None:
                raise ValidationError({'challenge': ['This player or team has already joined.']})
            raise ValidationError({'challenge': ['A withdrawn entry cannot rejoin this challenge.']})
        try:
            entry = ChallengeEntry.objects.create(challenge=challenge, **lookup)
        except IntegrityError as exc:
            raise ValidationError({'challenge': ['This player or team has already joined.']}) from exc
        if challenge.kind == SportChallenge.Kind.TEAM:
            ChallengeEntryMember.objects.bulk_create([
                ChallengeEntryMember(entry=entry, user=member.user) for member in members
            ])
        return entry

    @classmethod
    @transaction.atomic
    def leave(cls, *, entry, user):
        entry = ChallengeEntry.objects.select_for_update().select_related('challenge', 'team', 'user').get(pk=entry.pk)
        if entry.left_at:
            raise ValidationError({'challenge': ['This entry has already withdrawn.']})
        if entry.user_id:
            if entry.user_id != user.pk:
                raise PermissionDenied('You cannot withdraw another player.')
        elif not CommunityMember.objects.filter(
            community=entry.team, user=user, status=CommunityMember.Status.ACTIVE,
            role__in=(CommunityMember.Role.OWNER, CommunityMember.Role.CAPTAIN,
                      CommunityMember.Role.ADMIN),
        ).exists():
            raise PermissionDenied('Only a team owner, captain or admin can withdraw it.')
        if (ChallengeService.entry_progress(entry=entry) >= entry.challenge.target
                or ChallengeRewardGrant.objects.filter(entry=entry).exists()):
            raise ValidationError({'challenge': ['A completed challenge entry cannot be withdrawn.']})
        entry.left_at = timezone.now()
        entry.save(update_fields=['left_at', 'updated_at'])
        return entry

    @classmethod
    def serialize_entry(cls, entry, *, viewer):
        progress = cls.entry_progress(entry=entry)
        if entry.left_at is None and entry.challenge.is_active:
            cls._grant_rewards(entry=entry, progress=progress)
        recipient = entry.user_id == viewer.pk or entry.roster.filter(user=viewer).exists()
        grant = ChallengeRewardGrant.objects.filter(entry=entry, user=viewer).first() if recipient else None
        if grant:
            progress = max(progress, entry.challenge.target)
        return {
            'public_id': str(entry.public_id),
            'kind': entry.challenge.kind,
            'user_id': entry.user_id if entry.user_id else None,
            'team_id': str(entry.team.public_id) if entry.team_id else None,
            'joined_at': entry.joined_at,
            'left_at': entry.left_at,
            'progress': progress,
            'target': entry.challenge.target,
            'completed': bool(grant) or (entry.challenge.is_active and entry.left_at is None and progress >= entry.challenge.target),
            'reward_points': grant.points if grant else 0,
        }

    @classmethod
    def serialize_challenge(cls, challenge, *, viewer):
        entry = ChallengeEntry.objects.filter(challenge=challenge).filter(
            Q(user=viewer) | Q(roster__user=viewer),
        ).distinct().order_by('-created_at').first()
        return {
            'public_id': str(challenge.public_id), 'name': challenge.name,
            'description': challenge.description,
            'sport': {'id': challenge.sport_id, 'name': challenge.sport.name} if challenge.sport_id else None,
            'kind': challenge.kind, 'metric': challenge.metric, 'target': challenge.target,
            'eligibility_min_skill': challenge.eligibility_min_skill,
            'starts_on': challenge.starts_on, 'ends_on': challenge.ends_on,
            'is_active': challenge.is_active,
            'status': ('cancelled' if not challenge.is_active else
                       'upcoming' if challenge.starts_on > timezone.localdate() else
                       'completed' if challenge.ends_on < timezone.localdate() else 'active'),
            'reward_points': challenge.reward_points,
            'reward_achievement': challenge.reward_achievement.name if challenge.reward_achievement_id else None,
            'my_entry': cls.serialize_entry(entry, viewer=viewer) if entry else None,
        }

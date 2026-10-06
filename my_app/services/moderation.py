"""Transactional, auditable platform moderation.  API views never mutate targets directly."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from my_app.models import (
    Community, CommunityComment, CommunityPost, Event, Game, Message,
    ModerationAction, PlayerProfile, PlayerRating, Report, Tournament, User,
    UserModeration, VenueReview,
)
from my_app.services.notifications import NotificationService


TARGET_MODELS = {
    Report.TargetType.USER: User,
    Report.TargetType.PLAYER_PROFILE: PlayerProfile,
    Report.TargetType.GAME: Game,
    Report.TargetType.COMMUNITY: Community,
    Report.TargetType.COMMUNITY_POST: CommunityPost,
    Report.TargetType.COMMUNITY_COMMENT: CommunityComment,
    Report.TargetType.MESSAGE: Message,
    Report.TargetType.EVENT: Event,
    Report.TargetType.TOURNAMENT: Tournament,
    Report.TargetType.RATING: PlayerRating,
    Report.TargetType.VENUE_REVIEW: VenueReview,
}


class ModerationService:
    @staticmethod
    def target(*, target_type, target_public_id):
        model = TARGET_MODELS.get(target_type)
        if not model:
            raise ValidationError({'target_type': ['Unsupported moderation target.']})
        try:
            return model.objects.get(public_id=target_public_id)
        except model.DoesNotExist as exc:
            raise NotFound('The reported resource no longer exists.') from exc

    @classmethod
    @transaction.atomic
    def create_report(cls, *, reporter, target_type, target_public_id, reason, description=''):
        target = cls.target(target_type=target_type, target_public_id=target_public_id)
        target_user_id = getattr(target, 'user_id', None) or getattr(target, 'author_id', None) or getattr(target, 'sender_id', None)
        if target_type == Report.TargetType.USER:
            target_user_id = target.id
        if target_user_id == reporter.id:
            raise ValidationError({'target_public_id': ['You cannot report yourself.']})
        existing = Report.objects.filter(
            reporter=reporter, target_type=target_type, target_public_id=target_public_id,
            status__in=[Report.Status.PENDING, Report.Status.UNDER_REVIEW],
        ).first()
        if existing:
            return existing, False
        return Report.objects.create(
            reporter=reporter, target_type=target_type, target_public_id=target_public_id,
            reason=reason, description=description.strip(),
        ), True

    @staticmethod
    def _target_state(target):
        return {
            'account_status': getattr(target, 'account_status', None),
            'is_active': getattr(target, 'is_active', None),
            'visibility': getattr(target, 'visibility', None),
            'status': getattr(target, 'status', None),
            'deleted': bool(getattr(target, 'deleted_at', None)),
        }

    @staticmethod
    def _target_user(target, target_type):
        if target_type == Report.TargetType.USER:
            return target
        if target_type == Report.TargetType.PLAYER_PROFILE:
            return target.user
        for field in ('user', 'author', 'sender', 'reviewer', 'organizer', 'host', 'owner'):
            user = getattr(target, field, None)
            if user is not None:
                return user
        return None

    @classmethod
    @transaction.atomic
    def apply_action(cls, *, moderator, target_type, target_public_id, action, reason='', report=None, ends_at=None):
        target = cls.target(target_type=target_type, target_public_id=target_public_id)
        previous = cls._target_state(target)
        target_user = cls._target_user(target, target_type)
        now = timezone.now()

        if action in [ModerationAction.Action.SUSPEND, ModerationAction.Action.BAN, ModerationAction.Action.RESTORE, ModerationAction.Action.WARN] and target_type != Report.TargetType.USER:
            raise ValidationError({'action': ['User account actions require a user target.']})
        if action == ModerationAction.Action.SUSPEND:
            if ends_at is not None and ends_at <= now:
                raise ValidationError({'ends_at': ['Suspension end must be in the future.']})
            target.account_status = User.AccountStatus.SUSPENDED
            target.save(update_fields=['account_status', 'updated_at'])
            UserModeration.objects.create(user=target, moderator=moderator, action=action, reason=reason, starts_at=now, ends_at=ends_at)
        elif action == ModerationAction.Action.BAN:
            target.account_status = User.AccountStatus.DEACTIVATED
            target.is_active = False
            target.save(update_fields=['account_status', 'is_active', 'updated_at'])
            UserModeration.objects.create(user=target, moderator=moderator, action=action, reason=reason, starts_at=now)
        elif action == ModerationAction.Action.RESTORE:
            target.account_status = User.AccountStatus.ACTIVE
            target.is_active = True
            target.save(update_fields=['account_status', 'is_active', 'updated_at'])
            UserModeration.objects.filter(user=target, status=UserModeration.Status.ACTIVE).update(status=UserModeration.Status.ENDED, updated_at=now)
        elif action == ModerationAction.Action.WARN:
            NotificationService.create_notification(user=target, type='moderation_warning', title='SportMate account warning', body=reason or 'Your account has received a moderation warning.', event_key=f'moderation-warning:{target.public_id}:{now.isoformat()}')
        elif action in [ModerationAction.Action.HIDE, ModerationAction.Action.REMOVE]:
            if hasattr(target, 'deleted_at'):
                target.deleted_at = now
                if hasattr(target, 'content'):
                    target.content = ''
                    target.save(update_fields=['deleted_at', 'content', 'updated_at'])
                else:
                    target.save(update_fields=['deleted_at', 'updated_at'])
            elif isinstance(target, Community):
                target.is_active = False
                target.save(update_fields=['is_active', 'updated_at'])
            elif isinstance(target, (Game, Event, Tournament)):
                target.status = target.Status.CANCELLED
                target.save(update_fields=['status', 'updated_at'])
            elif isinstance(target, PlayerProfile):
                target.profile_visibility = 'private'
                target.save(update_fields=['profile_visibility', 'updated_at'])
            else:
                raise ValidationError({'action': ['This target does not support content removal.']})
        else:
            raise ValidationError({'action': ['Unsupported moderation action.']})

        action_log = ModerationAction.objects.create(
            moderator=moderator, report=report, target_type=target_type,
            target_public_id=target_public_id, action=action, reason=reason,
            previous_state=previous, new_state=cls._target_state(target),
        )
        if target_user and action in [ModerationAction.Action.SUSPEND, ModerationAction.Action.BAN, ModerationAction.Action.RESTORE]:
            NotificationService.create_notification(user=target_user, type='moderation_outcome', title='SportMate account update', body=reason or 'Your account moderation status has changed.', event_key=f'moderation:{action_log.public_id}')
        return action_log

    @classmethod
    @transaction.atomic
    def update_report(cls, *, report, moderator, status, resolution='', assigned_moderator=None):
        if assigned_moderator is not None:
            report.assigned_moderator = assigned_moderator
        report.status = status
        report.resolution = resolution.strip()
        if status in [Report.Status.RESOLVED, Report.Status.DISMISSED]:
            report.resolved_at = timezone.now()
            NotificationService.create_notification(user=report.reporter, type='report_resolved', title='Report updated', body='Your report has been reviewed.', event_key=f'report-resolution:{report.public_id}:{status}')
        report.save(update_fields=['assigned_moderator', 'status', 'resolution', 'resolved_at', 'updated_at'])
        return report

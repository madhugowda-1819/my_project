from django.conf import settings
from django.db import models

from my_app.models import TimestampedPublicModel


class RoleAuditLog(TimestampedPublicModel):
    """Auditable role and platform-operations changes owned by the admin portal."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='role_audit_actions',
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='role_audit_entries',
    )
    role = models.CharField(max_length=64)
    action = models.CharField(max_length=16)
    reason = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = 'my_app_roleauditlog'


class Report(TimestampedPublicModel):
    class TargetType(models.TextChoices):
        USER = 'user', 'User'
        PLAYER_PROFILE = 'player_profile', 'Player profile'
        GAME = 'game', 'Game'
        COMMUNITY = 'community', 'Community'
        COMMUNITY_POST = 'community_post', 'Community post'
        COMMUNITY_COMMENT = 'community_comment', 'Community comment'
        MESSAGE = 'message', 'Message'
        EVENT = 'event', 'Event'
        TOURNAMENT = 'tournament', 'Tournament'
        RATING = 'rating', 'Rating'
        VENUE_REVIEW = 'venue_review', 'Venue review'

    class Reason(models.TextChoices):
        SPAM = 'spam', 'Spam'
        HARASSMENT = 'harassment', 'Harassment'
        ABUSE = 'abuse', 'Abuse'
        INAPPROPRIATE_CONTENT = 'inappropriate_content', 'Inappropriate content'
        FAKE_PROFILE = 'fake_profile', 'Fake profile'
        FRAUD = 'fraud', 'Fraud'
        CHEATING = 'cheating', 'Cheating'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        UNDER_REVIEW = 'under_review', 'Under review'
        RESOLVED = 'resolved', 'Resolved'
        DISMISSED = 'dismissed', 'Dismissed'

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='reports_created')
    target_type = models.CharField(max_length=24, choices=TargetType.choices, db_index=True)
    target_public_id = models.UUIDField(db_index=True)
    reason = models.CharField(max_length=24, choices=Reason.choices, db_index=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    assigned_moderator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='reports_assigned')
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'my_app_report'
        indexes = [
            models.Index(fields=['status', 'created_at'], name='report_status_created_idx'),
            models.Index(fields=['target_type', 'target_public_id'], name='report_target_idx'),
            models.Index(fields=['reporter', 'created_at'], name='report_reporter_created_idx'),
        ]


class ModerationAction(TimestampedPublicModel):
    class Action(models.TextChoices):
        WARN = 'warn', 'Warn'
        HIDE = 'hide', 'Hide'
        REMOVE = 'remove', 'Remove'
        SUSPEND = 'suspend', 'Suspend'
        BAN = 'ban', 'Ban'
        RESTORE = 'restore', 'Restore'

    moderator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='moderation_actions')
    report = models.ForeignKey(Report, null=True, blank=True, on_delete=models.SET_NULL, related_name='actions')
    target_type = models.CharField(max_length=24, choices=Report.TargetType.choices, db_index=True)
    target_public_id = models.UUIDField(db_index=True)
    action = models.CharField(max_length=12, choices=Action.choices, db_index=True)
    reason = models.TextField(blank=True)
    previous_state = models.JSONField(default=dict, blank=True)
    new_state = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'my_app_moderationaction'
        indexes = [models.Index(fields=['target_type', 'target_public_id', 'created_at'], name='moderation_target_time_idx')]


class UserModeration(TimestampedPublicModel):
    """Current and historical platform restrictions; never client writable."""
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        ENDED = 'ended', 'Ended'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='moderation_records')
    moderator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='user_moderations_issued')
    action = models.CharField(max_length=12, choices=ModerationAction.Action.choices)
    reason = models.TextField(blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)

    class Meta:
        db_table = 'my_app_usermoderation'
        indexes = [models.Index(fields=['user', 'status'], name='user_moderation_status_idx')]

"""Serializers for platform-administration responses."""
from rest_framework import serializers

from my_app.models import User
from my_app_admin_portal.models import ModerationAction, Report


class ModerationActionSerializer(serializers.ModelSerializer):
    moderator_id = serializers.UUIDField(source='moderator.public_id', read_only=True)
    report_id = serializers.UUIDField(source='report.public_id', read_only=True, allow_null=True)

    class Meta:
        model = ModerationAction
        fields = ['public_id', 'moderator_id', 'report_id', 'target_type', 'target_public_id', 'action', 'reason', 'previous_state', 'new_state', 'created_at']
        read_only_fields = fields


class AdminUserSerializer(serializers.ModelSerializer):
    public_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = User
        fields = ['public_id', 'username', 'email', 'full_name', 'city', 'account_status', 'is_active', 'is_staff', 'date_joined', 'last_login']


class ReportCreateSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(choices=Report.TargetType.choices)
    target_public_id = serializers.UUIDField()
    reason = serializers.ChoiceField(choices=Report.Reason.choices)
    description = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class ReportSerializer(serializers.ModelSerializer):
    reporter_id = serializers.UUIDField(source='reporter.public_id', read_only=True)
    assigned_moderator_id = serializers.UUIDField(source='assigned_moderator.public_id', read_only=True, allow_null=True)

    class Meta:
        model = Report
        fields = ['public_id', 'reporter_id', 'target_type', 'target_public_id', 'reason', 'description', 'status', 'assigned_moderator_id', 'resolution', 'created_at', 'updated_at', 'resolved_at']
        read_only_fields = fields


class ReportUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Report.Status.choices, required=False)
    resolution = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    assigned_moderator_id = serializers.UUIDField(required=False, allow_null=True)
    action = serializers.ChoiceField(choices=ModerationAction.Action.choices, required=False)
    action_reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    ends_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError('At least one update is required.')
        return attrs

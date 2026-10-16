import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def move_content_types(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    for name in ('report', 'moderationaction', 'usermoderation'):
        ContentType.objects.filter(app_label='my_app', model=name).update(app_label='my_app_admin_portal')


def restore_content_types(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    for name in ('report', 'moderationaction', 'usermoderation'):
        ContentType.objects.filter(app_label='my_app_admin_portal', model=name).update(app_label='my_app')


class Migration(migrations.Migration):
    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('my_app', '0055_transfer_portal_model_states'),
        ('my_app_admin_portal', '0001_role_audit_log'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='Report',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('target_type', models.CharField(choices=[('user', 'User'), ('player_profile', 'Player profile'), ('game', 'Game'), ('community', 'Community'), ('community_post', 'Community post'), ('community_comment', 'Community comment'), ('message', 'Message'), ('event', 'Event'), ('tournament', 'Tournament'), ('rating', 'Rating'), ('venue_review', 'Venue review')], db_index=True, max_length=24)),
                        ('target_public_id', models.UUIDField(db_index=True)),
                        ('reason', models.CharField(choices=[('spam', 'Spam'), ('harassment', 'Harassment'), ('abuse', 'Abuse'), ('inappropriate_content', 'Inappropriate content'), ('fake_profile', 'Fake profile'), ('fraud', 'Fraud'), ('cheating', 'Cheating'), ('other', 'Other')], db_index=True, max_length=24)),
                        ('description', models.TextField(blank=True)),
                        ('status', models.CharField(choices=[('pending', 'Pending'), ('under_review', 'Under review'), ('resolved', 'Resolved'), ('dismissed', 'Dismissed')], db_index=True, default='pending', max_length=16)),
                        ('resolution', models.TextField(blank=True)),
                        ('resolved_at', models.DateTimeField(blank=True, null=True)),
                        ('assigned_moderator', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports_assigned', to=settings.AUTH_USER_MODEL)),
                        ('reporter', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reports_created', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={'db_table': 'my_app_report', 'indexes': [models.Index(fields=['status', 'created_at'], name='report_status_created_idx'), models.Index(fields=['target_type', 'target_public_id'], name='report_target_idx'), models.Index(fields=['reporter', 'created_at'], name='report_reporter_created_idx')]},
                ),
                migrations.CreateModel(
                    name='ModerationAction',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('target_type', models.CharField(choices=[('user', 'User'), ('player_profile', 'Player profile'), ('game', 'Game'), ('community', 'Community'), ('community_post', 'Community post'), ('community_comment', 'Community comment'), ('message', 'Message'), ('event', 'Event'), ('tournament', 'Tournament'), ('rating', 'Rating'), ('venue_review', 'Venue review')], db_index=True, max_length=24)),
                        ('target_public_id', models.UUIDField(db_index=True)),
                        ('action', models.CharField(choices=[('warn', 'Warn'), ('hide', 'Hide'), ('remove', 'Remove'), ('suspend', 'Suspend'), ('ban', 'Ban'), ('restore', 'Restore')], db_index=True, max_length=12)),
                        ('reason', models.TextField(blank=True)),
                        ('previous_state', models.JSONField(blank=True, default=dict)),
                        ('new_state', models.JSONField(blank=True, default=dict)),
                        ('moderator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='moderation_actions', to=settings.AUTH_USER_MODEL)),
                        ('report', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='actions', to='my_app_admin_portal.report')),
                    ],
                    options={'db_table': 'my_app_moderationaction', 'indexes': [models.Index(fields=['target_type', 'target_public_id', 'created_at'], name='moderation_target_time_idx')]},
                ),
                migrations.CreateModel(
                    name='UserModeration',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('action', models.CharField(choices=[('warn', 'Warn'), ('hide', 'Hide'), ('remove', 'Remove'), ('suspend', 'Suspend'), ('ban', 'Ban'), ('restore', 'Restore')], max_length=12)),
                        ('reason', models.TextField(blank=True)),
                        ('starts_at', models.DateTimeField()),
                        ('ends_at', models.DateTimeField(blank=True, null=True)),
                        ('status', models.CharField(choices=[('active', 'Active'), ('ended', 'Ended')], db_index=True, default='active', max_length=10)),
                        ('moderator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='user_moderations_issued', to=settings.AUTH_USER_MODEL)),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='moderation_records', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={'db_table': 'my_app_usermoderation', 'indexes': [models.Index(fields=['user', 'status'], name='user_moderation_status_idx')]},
                ),
            ],
        ),
        migrations.RunPython(move_content_types, restore_content_types),
    ]

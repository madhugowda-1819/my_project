import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0021_recommendation_profile')]

    operations = [
        migrations.CreateModel(
            name='Report',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('target_type', models.CharField(db_index=True, max_length=24)), ('target_public_id', models.UUIDField(db_index=True)),
                ('reason', models.CharField(db_index=True, max_length=24)), ('description', models.TextField(blank=True)),
                ('status', models.CharField(db_index=True, default='pending', max_length=16)), ('resolution', models.TextField(blank=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('assigned_moderator', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reports_assigned', to=settings.AUTH_USER_MODEL)),
                ('reporter', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reports_created', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ModerationAction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('target_type', models.CharField(db_index=True, max_length=24)), ('target_public_id', models.UUIDField(db_index=True)),
                ('action', models.CharField(db_index=True, max_length=12)), ('reason', models.TextField(blank=True)),
                ('previous_state', models.JSONField(blank=True, default=dict)), ('new_state', models.JSONField(blank=True, default=dict)),
                ('moderator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='moderation_actions', to=settings.AUTH_USER_MODEL)),
                ('report', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='actions', to='my_app.report')),
            ],
        ),
        migrations.CreateModel(
            name='UserModeration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(max_length=12)), ('reason', models.TextField(blank=True)),
                ('starts_at', models.DateTimeField()), ('ends_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(db_index=True, default='active', max_length=10)),
                ('moderator', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='user_moderations_issued', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='moderation_records', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(model_name='report', index=models.Index(fields=['status', 'created_at'], name='report_status_created_idx')),
        migrations.AddIndex(model_name='report', index=models.Index(fields=['target_type', 'target_public_id'], name='report_target_idx')),
        migrations.AddIndex(model_name='report', index=models.Index(fields=['reporter', 'created_at'], name='report_reporter_created_idx')),
        migrations.AddIndex(model_name='moderationaction', index=models.Index(fields=['target_type', 'target_public_id', 'created_at'], name='moderation_target_time_idx')),
        migrations.AddIndex(model_name='usermoderation', index=models.Index(fields=['user', 'status'], name='user_moderation_status_idx')),
    ]

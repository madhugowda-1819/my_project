# Generated manually for the additive Phase 9 game system.
import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('my_app', '0013_merge_20261005_2100'),
    ]

    operations = [
        migrations.CreateModel(
            name='Game',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('game_reference', models.CharField(db_index=True, max_length=20, unique=True)),
                ('game_date', models.DateField(db_index=True)),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('starts_at', models.DateTimeField(db_index=True)),
                ('ends_at', models.DateTimeField(db_index=True)),
                ('min_players', models.PositiveIntegerField(default=2)),
                ('max_players', models.PositiveIntegerField()),
                ('skill_level', models.CharField(blank=True, choices=[('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced'), ('expert', 'Expert'), ('pro', 'Pro')], max_length=16)),
                ('description', models.TextField(blank=True)),
                ('visibility', models.CharField(choices=[('public', 'Public'), ('private', 'Private')], db_index=True, default='public', max_length=10)),
                ('status', models.CharField(choices=[('open', 'Open'), ('almost_full', 'Almost full'), ('full', 'Full'), ('started', 'Started'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], db_index=True, default='open', max_length=16)),
                ('court', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='games', to='my_app.court')),
                ('host', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='hosted_games', to=settings.AUTH_USER_MODEL)),
                ('sport', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='games', to='my_app.sport')),
                ('venue', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='games', to='my_app.venue')),
            ],
        ),
        migrations.CreateModel(
            name='GamePlayer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('confirmed', 'Confirmed'), ('left', 'Left'), ('removed', 'Removed')], db_index=True, default='confirmed', max_length=12)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('left_at', models.DateTimeField(blank=True, null=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='game_players', to='my_app.game')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='game_participations', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(model_name='game', index=models.Index(fields=['sport', 'game_date', 'status'], name='game_sport_date_status_idx')),
        migrations.AddIndex(model_name='game', index=models.Index(fields=['venue', 'game_date'], name='game_venue_date_idx')),
        migrations.AddIndex(model_name='game', index=models.Index(fields=['court', 'starts_at', 'status'], name='game_court_time_status_idx')),
        migrations.AddIndex(model_name='game', index=models.Index(fields=['host', 'status'], name='game_host_status_idx')),
        migrations.AddConstraint(model_name='game', constraint=models.CheckConstraint(check=models.Q(('end_time__gt', models.F('start_time'))), name='game_end_after_start')),
        migrations.AddConstraint(model_name='game', constraint=models.CheckConstraint(check=models.Q(('min_players__gt', 0)), name='game_min_players_positive')),
        migrations.AddConstraint(model_name='game', constraint=models.CheckConstraint(check=models.Q(('max_players__gt', 0)), name='game_max_players_positive')),
        migrations.AddConstraint(model_name='game', constraint=models.CheckConstraint(check=models.Q(('min_players__lte', models.F('max_players'))), name='game_min_lte_max')),
        migrations.AddConstraint(model_name='game', constraint=models.CheckConstraint(check=models.Q(('ends_at__gt', models.F('starts_at'))), name='game_datetime_range_valid')),
        migrations.AddIndex(model_name='gameplayer', index=models.Index(fields=['game', 'status'], name='game_player_game_status_idx')),
        migrations.AddIndex(model_name='gameplayer', index=models.Index(fields=['user', 'status'], name='game_player_user_status_idx')),
        migrations.AddConstraint(model_name='gameplayer', constraint=models.UniqueConstraint(condition=models.Q(('status', 'confirmed')), fields=('game', 'user'), name='unique_active_game_membership')),
    ]

import uuid

from django.db import migrations, models
from django.db.models import F, Q
from django.utils import timezone


PUBLIC_MODELS = ('user', 'sport', 'playerprofile', 'ground', 'availabilityslot', 'match', 'message', 'notification')


def populate_public_ids(apps, schema_editor):
    for model_name in PUBLIC_MODELS:
        model = apps.get_model('my_app', model_name)
        for instance in model.objects.filter(public_id__isnull=True).iterator():
            instance.public_id = uuid.uuid4()
            instance.save(update_fields=['public_id'])


class Migration(migrations.Migration):
    dependencies = [('my_app', '0003_user_full_name')]

    operations = [
        *[
            migrations.AddField(
                model_name=model_name,
                name='public_id',
                field=models.UUIDField(blank=True, db_index=True, editable=False, null=True),
            )
            for model_name in PUBLIC_MODELS
        ],
        *[
            migrations.AddField(
                model_name=model_name,
                name='created_at',
                field=models.DateTimeField(default=timezone.now),
                preserve_default=False,
            )
            for model_name in ('sport', 'playerprofile', 'ground', 'availabilityslot', 'match')
        ],
        *[
            migrations.AddField(
                model_name=model_name,
                name='updated_at',
                field=models.DateTimeField(default=timezone.now),
                preserve_default=False,
            )
            for model_name in PUBLIC_MODELS
        ],
        migrations.RunPython(populate_public_ids, migrations.RunPython.noop),
        *[
            migrations.AlterField(
                model_name=model_name,
                name='public_id',
                field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True),
            )
            for model_name in PUBLIC_MODELS
        ],
        *[
            migrations.AlterField(
                model_name=model_name,
                name='created_at',
                field=models.DateTimeField(auto_now_add=True),
            )
            for model_name in ('sport', 'playerprofile', 'ground', 'availabilityslot', 'match')
        ],
        *[
            migrations.AlterField(
                model_name=model_name,
                name='updated_at',
                field=models.DateTimeField(auto_now=True),
            )
            for model_name in PUBLIC_MODELS
        ],
        migrations.AddConstraint(
            model_name='playerprofile',
            constraint=models.CheckConstraint(check=Q(matches_won__lte=F('matches_played')), name='profile_wins_cannot_exceed_matches_played'),
        ),
        migrations.AddConstraint(model_name='ground', constraint=models.CheckConstraint(check=Q(price_per_hour__gte=0), name='ground_price_is_non_negative')),
        migrations.AddConstraint(model_name='ground', constraint=models.CheckConstraint(check=Q(latitude__gte=-90, latitude__lte=90), name='ground_latitude_is_valid')),
        migrations.AddConstraint(model_name='ground', constraint=models.CheckConstraint(check=Q(longitude__gte=-180, longitude__lte=180), name='ground_longitude_is_valid')),
        migrations.AddConstraint(model_name='availabilityslot', constraint=models.CheckConstraint(check=Q(day_of_week__gte=0, day_of_week__lte=6), name='availability_day_is_valid')),
        migrations.AddConstraint(model_name='availabilityslot', constraint=models.CheckConstraint(check=Q(end_time__gt=F('start_time')), name='availability_end_after_start')),
        migrations.AddConstraint(model_name='availabilityslot', constraint=models.UniqueConstraint(fields=('user', 'day_of_week', 'start_time', 'end_time'), name='unique_availability_slot')),
        migrations.AddConstraint(model_name='match', constraint=models.CheckConstraint(check=Q(total_players__gt=0), name='match_total_players_is_positive')),
        migrations.AddConstraint(model_name='message', constraint=models.CheckConstraint(check=~Q(sender=F('receiver')), name='message_sender_differs_from_receiver')),
        migrations.AddIndex(model_name='ground', index=models.Index(fields=['city', 'name'], name='ground_city_name_idx')),
        migrations.AddIndex(model_name='availabilityslot', index=models.Index(fields=['user', 'day_of_week', 'start_time'], name='availability_user_time_idx')),
        migrations.AddIndex(model_name='match', index=models.Index(fields=['status', 'date_time'], name='match_status_date_idx')),
        migrations.AddIndex(model_name='match', index=models.Index(fields=['sport', 'date_time'], name='match_sport_date_idx')),
        migrations.AddIndex(model_name='match', index=models.Index(fields=['ground', 'date_time'], name='match_ground_date_idx')),
        migrations.AddIndex(model_name='message', index=models.Index(fields=['receiver', 'is_read', 'created_at'], name='message_receiver_read_idx')),
        migrations.AddIndex(model_name='notification', index=models.Index(fields=['user', 'is_read', 'created_at'], name='notification_user_read_idx')),
    ]

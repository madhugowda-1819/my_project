from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('my_app', '0049_notification_query_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='venue',
            name='source_nearby_ground',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tournament_venue',
                to='my_app.sportsground',
            ),
        ),
    ]

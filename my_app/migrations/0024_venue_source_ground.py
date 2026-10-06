from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('my_app', '0023_remove_notification_notification_user_read_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='venue',
            name='source_ground',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='verified_venue',
                to='my_app.ground',
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0031_match_end_date_time')]

    operations = [
        migrations.AddField(
            model_name='playerprofile',
            name='matching_mood',
            field=models.CharField(
                blank=True,
                choices=[('relaxed', 'Relaxed'), ('energetic', 'Energetic'), ('learning', 'Learning'), ('social', 'Social')],
                default='',
                max_length=16,
            ),
        ),
    ]

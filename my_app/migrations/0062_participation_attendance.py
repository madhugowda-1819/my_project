from django.db import migrations, models


def mark_existing_participants_attended(apps, schema_editor):
    MatchParticipation = apps.get_model('my_app', 'MatchParticipation')
    GamePlayer = apps.get_model('my_app', 'GamePlayer')
    MatchParticipation.objects.filter(status='joined').update(attendance='attended')
    GamePlayer.objects.filter(status='confirmed').update(attendance='attended')


class Migration(migrations.Migration):
    dependencies = [('my_app', '0061_match_title_description')]
    operations = [
        migrations.AddField(
            model_name='matchparticipation', name='attendance',
            field=models.CharField(choices=[('pending', 'Attendance pending'), ('attended', 'Attended'), ('no_show', 'No-show')], db_index=True, default='pending', max_length=12),
        ),
        migrations.AddField(
            model_name='gameplayer', name='attendance',
            field=models.CharField(choices=[('pending', 'Attendance pending'), ('attended', 'Attended'), ('no_show', 'No-show')], db_index=True, default='pending', max_length=12),
        ),
        migrations.RunPython(mark_existing_participants_attended, migrations.RunPython.noop),
    ]

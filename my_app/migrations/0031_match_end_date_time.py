from datetime import timedelta

from django.db import migrations, models


def set_legacy_end_times(apps, schema_editor):
    Match = apps.get_model('my_app', 'Match')
    for match in Match.objects.all().only('id', 'date_time'):
        Match.objects.filter(pk=match.pk).update(end_date_time=match.date_time + timedelta(hours=1))


class Migration(migrations.Migration):
    dependencies = [('my_app', '0030_player_rating_categories')]

    operations = [
        migrations.AddField(model_name='match', name='end_date_time', field=models.DateTimeField(null=True)),
        migrations.RunPython(set_legacy_end_times, migrations.RunPython.noop),
        migrations.AlterField(model_name='match', name='end_date_time', field=models.DateTimeField()),
        migrations.AddConstraint(model_name='match', constraint=models.CheckConstraint(check=models.Q(('end_date_time__gt', models.F('date_time'))), name='match_end_after_start')),
    ]

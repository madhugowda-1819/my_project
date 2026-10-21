from django.db import migrations, models
import django.db.models.deletion
import uuid


FORMATS = {
    'cricket': [('Recruitment (11–15)', 11, 15, 11)],
    'football': [('Team', 2, 11, 11)],
    'badminton': [('Singles', 2, 2, 2), ('Doubles', 4, 4, 4)],
    'basketball': [('Team', 2, 5, 5)],
    'volleyball': [('Team', 2, 6, 6)],
    'kabaddi': [('Team', 2, 7, 7)],
    'kho-kho': [('Team', 2, 9, 9)],
    'chess': [('Singles', 2, 2, 2)],
    'table-tennis': [('Singles', 2, 2, 2), ('Doubles', 4, 4, 4)],
    'carrom': [('Singles', 2, 2, 2)],
}


def seed(apps, schema_editor):
    Sport = apps.get_model('my_app', 'Sport')
    SportFormat = apps.get_model('my_app', 'SportFormat')
    for slug, formats in FORMATS.items():
        sport = Sport.objects.filter(slug=slug).first()
        if not sport:
            continue
        for name, minimum, maximum, default in formats:
            SportFormat.objects.update_or_create(
                sport=sport, name=name,
                defaults={'minimum_players': minimum, 'maximum_players': maximum, 'default_capacity': default, 'is_active': True},
            )


class Migration(migrations.Migration):
    dependencies = [('my_app', '0059_sportsground_expires_at')]
    operations = [
        migrations.CreateModel(
            name='SportFormat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=80)),
                ('minimum_players', models.PositiveIntegerField()),
                ('maximum_players', models.PositiveIntegerField()),
                ('default_capacity', models.PositiveIntegerField()),
                ('is_active', models.BooleanField(default=True)),
                ('sport', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='formats', to='my_app.sport')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('sport', 'name'), name='unique_sport_format_name'), models.CheckConstraint(check=models.Q(('minimum_players__gt', 0)), name='sport_format_min_positive'), models.CheckConstraint(check=models.Q(('minimum_players__lte', models.F('maximum_players'))), name='sport_format_min_lte_max'), models.CheckConstraint(check=models.Q(('default_capacity__gte', models.F('minimum_players')), ('default_capacity__lte', models.F('maximum_players'))), name='sport_format_default_in_range')]},
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]

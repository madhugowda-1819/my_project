from django.db import migrations


CORE_SPORTS = (
    ('Badminton', 'badminton'),
    ('Football', 'football'),
    ('Cricket', 'cricket'),
    ('Tennis', 'tennis'),
    ('Basketball', 'basketball'),
    ('Volleyball', 'volleyball'),
    ('Table Tennis', 'table-tennis'),
    ('Pickleball', 'pickleball'),
    ('Swimming', 'swimming'),
)


def seed_core_sports(apps, schema_editor):
    sport_model = apps.get_model('my_app', 'Sport')
    for name, slug in CORE_SPORTS:
        sport_model.objects.get_or_create(slug=slug, defaults={'name': name, 'is_active': True})


class Migration(migrations.Migration):
    dependencies = [('my_app', '0006_usersport_alter_sport_options_and_more')]
    operations = [migrations.RunPython(seed_core_sports, migrations.RunPython.noop)]

from django.db import migrations


ADDITIONAL_SPORTS = (
    ('Carrom', 'carrom'),
    ('Chess', 'chess'),
    ('Kabaddi', 'kabaddi'),
    ('Kho Kho', 'kho-kho'),
)


def seed_additional_sports(apps, schema_editor):
    Sport = apps.get_model('my_app', 'Sport')
    for name, slug in ADDITIONAL_SPORTS:
        Sport.objects.update_or_create(
            slug=slug,
            defaults={'name': name, 'is_active': True},
        )


def remove_additional_sports(apps, schema_editor):
    Sport = apps.get_model('my_app', 'Sport')
    Sport.objects.filter(slug__in=[slug for _, slug in ADDITIONAL_SPORTS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('my_app', '0057_challenge_reward_grant'),
    ]

    operations = [
        migrations.RunPython(seed_additional_sports, remove_additional_sports),
    ]

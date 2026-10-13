from django.db import migrations, models


def mark_existing_demo_venues(apps, schema_editor):
    Venue = apps.get_model('my_app', 'Venue')
    Venue.objects.using(schema_editor.connection.alias).filter(
        name='SportMate Demo Arena',
    ).update(is_demo=True, active=False)


class Migration(migrations.Migration):
    dependencies = [('my_app', '0047_user_referral_code_alter_notification_type_and_more')]

    operations = [
        migrations.AddField(
            model_name='venue',
            name='is_demo',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RunPython(mark_existing_demo_venues, migrations.RunPython.noop),
    ]

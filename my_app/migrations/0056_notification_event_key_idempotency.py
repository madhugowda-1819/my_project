from django.db import migrations, models


def make_existing_keys_unique(apps, schema_editor):
    """Retain notification rows while clearing duplicate idempotency markers."""
    Notification = apps.get_model('my_app', 'Notification')
    alias = schema_editor.connection.alias
    seen = set()
    duplicates = []
    rows = Notification.objects.using(alias).exclude(event_key='').order_by('created_at', 'id')
    for user_id, event_key, pk in rows.values_list('user_id', 'event_key', 'pk').iterator():
        key = (user_id, event_key)
        if key in seen:
            duplicates.append(pk)
        else:
            seen.add(key)
    if duplicates:
        Notification.objects.using(alias).filter(pk__in=duplicates).update(event_key='')


class Migration(migrations.Migration):
    dependencies = [('my_app', '0055_transfer_portal_model_states')]

    operations = [
        migrations.RunPython(make_existing_keys_unique, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.UniqueConstraint(
                fields=('user', 'event_key'),
                condition=~models.Q(event_key=''),
                name='notification_user_event_key_unique',
            ),
        ),
    ]

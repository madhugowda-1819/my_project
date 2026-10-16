from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('my_app', '0053_message_client_idempotency'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[migrations.DeleteModel(name='RoleAuditLog')],
        ),
    ]

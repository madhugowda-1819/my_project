from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('my_app', '0054_transfer_roleauditlog_state')]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='VenueOwnerApplication'),
                migrations.DeleteModel(name='UserModeration'),
                migrations.DeleteModel(name='ModerationAction'),
                migrations.DeleteModel(name='Report'),
            ],
        ),
    ]

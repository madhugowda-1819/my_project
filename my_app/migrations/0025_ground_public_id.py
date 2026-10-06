import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('my_app', '0024_venue_source_ground'),
    ]

    operations = [
        migrations.AddField(
            model_name='ground',
            name='public_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True),
        ),
    ]

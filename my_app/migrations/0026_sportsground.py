# Generated manually for the OSM nearby-ground cache.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0025_ground_public_id')]

    operations = [
        migrations.CreateModel(
            name='SportsGround',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('osm_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('ground_type', models.CharField(db_index=True, max_length=100)),
                ('sport', models.CharField(db_index=True, max_length=100)),
                ('latitude', models.FloatField(db_index=True)),
                ('longitude', models.FloatField(db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'indexes': [models.Index(fields=['latitude', 'longitude'], name='sports_ground_coords_idx')]},
        ),
    ]

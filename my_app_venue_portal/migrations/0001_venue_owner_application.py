import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def move_content_type(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='my_app', model='venueownerapplication').update(app_label='my_app_venue_portal')


def restore_content_type(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='my_app_venue_portal', model='venueownerapplication').update(app_label='my_app')


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('my_app', '0055_transfer_portal_model_states'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='VenueOwnerApplication',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('business_name', models.CharField(max_length=200)),
                        ('phone', models.CharField(max_length=20)),
                        ('city', models.CharField(max_length=100)),
                        ('address', models.CharField(max_length=255)),
                        ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], db_index=True, default='pending', max_length=16)),
                        ('review_note', models.CharField(blank=True, max_length=500)),
                        ('applicant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='venue_owner_applications', to=settings.AUTH_USER_MODEL)),
                        ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_venue_owner_applications', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={'db_table': 'my_app_venueownerapplication'},
                ),
            ],
        ),
        migrations.RunPython(move_content_type, restore_content_type),
    ]

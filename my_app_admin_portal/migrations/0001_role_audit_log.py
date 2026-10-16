import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def rename_role_audit_content_type(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='my_app', model='roleauditlog').update(
        app_label='my_app_admin_portal',
    )


def restore_role_audit_content_type(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ContentType.objects.filter(app_label='my_app_admin_portal', model='roleauditlog').update(
        app_label='my_app',
    )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('my_app', '0054_transfer_roleauditlog_state'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='RoleAuditLog',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('role', models.CharField(max_length=64)),
                        ('action', models.CharField(max_length=16)),
                        ('reason', models.CharField(blank=True, max_length=500)),
                        ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='role_audit_actions', to=settings.AUTH_USER_MODEL)),
                        ('target_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='role_audit_entries', to=settings.AUTH_USER_MODEL)),
                    ],
                    options={
                        'db_table': 'my_app_roleauditlog',
                    },
                ),
            ],
        ),
        migrations.RunPython(rename_role_audit_content_type, restore_role_audit_content_type),
    ]

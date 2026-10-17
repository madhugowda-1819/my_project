from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('my_app_admin_portal', '0002_moderation_models'),
    ]

    operations = [
        migrations.AlterField(
            model_name='roleauditlog',
            name='target_user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='role_audit_entries',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]

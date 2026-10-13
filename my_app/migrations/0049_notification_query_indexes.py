from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('my_app', '0048_venue_is_demo'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['user', 'deleted_at', '-created_at'],
                name='notif_user_del_created_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['user', 'is_read', 'deleted_at'],
                name='notif_user_unread_idx',
            ),
        ),
    ]

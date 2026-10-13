from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0032_playerprofile_matching_mood')]

    operations = [
        migrations.AddField('community', 'is_club', models.BooleanField(db_index=True, default=False)),
        migrations.AddField('community', 'membership_open', models.BooleanField(default=True)),
        migrations.AlterField(
            model_name='communitymember', name='role',
            field=models.CharField(choices=[('owner', 'Owner'), ('captain', 'Captain'), ('admin', 'Admin'), ('moderator', 'Moderator'), ('member', 'Member')], db_index=True, max_length=12, default='member'),
        ),
    ]

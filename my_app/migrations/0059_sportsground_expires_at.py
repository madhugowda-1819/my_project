from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0058_seed_additional_sports')]
    operations = [migrations.AddField(
        model_name='sportsground', name='expires_at',
        field=models.DateTimeField(blank=True, db_index=True, null=True),
    )]

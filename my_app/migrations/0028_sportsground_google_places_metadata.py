from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0027_paymenttransaction')]

    operations = [
        migrations.AddField(model_name='sportsground', name='google_rating', field=models.FloatField(blank=True, null=True)),
        migrations.AddField(model_name='sportsground', name='google_rating_count', field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.AddField(model_name='sportsground', name='google_photo_name', field=models.CharField(blank=True, max_length=512)),
    ]

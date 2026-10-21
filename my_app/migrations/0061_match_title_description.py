from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0060_sport_formats')]
    operations = [
        migrations.AddField(model_name='match', name='title', field=models.CharField(blank=True, default='', max_length=200)),
        migrations.AddField(model_name='match', name='description', field=models.TextField(blank=True, default='')),
    ]

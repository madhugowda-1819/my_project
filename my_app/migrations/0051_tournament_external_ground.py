from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('my_app', '0050_venue_source_nearby_ground'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tournament',
            name='venue',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='tournaments',
                to='my_app.venue',
            ),
        ),
        migrations.AddField(
            model_name='tournament',
            name='nearby_ground',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tournaments',
                to='my_app.sportsground',
            ),
        ),
    ]

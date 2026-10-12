from django.db import migrations, models


def copy_legacy_score(apps, schema_editor):
    PlayerRating = apps.get_model('my_app', 'PlayerRating')
    for item in PlayerRating.objects.all().only('id', 'rating'):
        PlayerRating.objects.filter(pk=item.pk).update(
            playing_performance=item.rating,
            teamwork=item.rating,
            sportsmanship=item.rating,
            punctuality=item.rating,
        )


class Migration(migrations.Migration):
    dependencies = [('my_app', '0029_venue_owner_roleauditlog_venueownerapplication')]

    operations = [
        migrations.AlterField(model_name='playerrating', name='rating', field=models.DecimalField(decimal_places=1, max_digits=3)),
        migrations.AddField(model_name='playerrating', name='playing_performance', field=models.PositiveSmallIntegerField(default=1), preserve_default=False),
        migrations.AddField(model_name='playerrating', name='teamwork', field=models.PositiveSmallIntegerField(default=1), preserve_default=False),
        migrations.AddField(model_name='playerrating', name='sportsmanship', field=models.PositiveSmallIntegerField(default=1), preserve_default=False),
        migrations.AddField(model_name='playerrating', name='punctuality', field=models.PositiveSmallIntegerField(default=1), preserve_default=False),
        migrations.RunPython(copy_legacy_score, migrations.RunPython.noop),
        migrations.AddConstraint(model_name='playerrating', constraint=models.CheckConstraint(check=models.Q(('playing_performance__gte', 1), ('playing_performance__lte', 5)), name='rating_performance_range_valid')),
        migrations.AddConstraint(model_name='playerrating', constraint=models.CheckConstraint(check=models.Q(('teamwork__gte', 1), ('teamwork__lte', 5)), name='rating_teamwork_range_valid')),
        migrations.AddConstraint(model_name='playerrating', constraint=models.CheckConstraint(check=models.Q(('sportsmanship__gte', 1), ('sportsmanship__lte', 5)), name='rating_sportsmanship_range_valid')),
        migrations.AddConstraint(model_name='playerrating', constraint=models.CheckConstraint(check=models.Q(('punctuality__gte', 1), ('punctuality__lte', 5)), name='rating_punctuality_range_valid')),
    ]

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models
class Migration(migrations.Migration):
 dependencies=[('my_app','0019_ratings_statistics')]
 operations=[
 migrations.CreateModel(name='Achievement',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('public_id',models.UUIDField(db_index=True,default=uuid.uuid4,editable=False,unique=True)),('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('code',models.CharField(db_index=True,max_length=64,unique=True)),('name',models.CharField(max_length=120)),('description',models.TextField()),('category',models.CharField(max_length=16)),('icon',models.CharField(blank=True,max_length=120)),('target_value',models.PositiveIntegerField(default=1)),('is_active',models.BooleanField(default=True))]),
 migrations.CreateModel(name='UserAchievement',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('public_id',models.UUIDField(db_index=True,default=uuid.uuid4,editable=False,unique=True)),('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('progress',models.PositiveIntegerField(default=0)),('unlocked',models.BooleanField(default=False)),('unlocked_at',models.DateTimeField(blank=True,null=True)),('achievement',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='user_achievements',to='my_app.achievement')),('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='achievements',to=settings.AUTH_USER_MODEL))]),
 migrations.AddConstraint(model_name='userachievement',constraint=models.UniqueConstraint(fields=('user','achievement'),name='unique_user_achievement'))]

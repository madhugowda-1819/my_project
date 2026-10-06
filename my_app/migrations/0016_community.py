import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0015_conversation_chat')]

    operations = [
        migrations.CreateModel(name='Community', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('name', models.CharField(max_length=150)), ('slug', models.SlugField(db_index=True, max_length=170, unique=True)), ('description', models.TextField(blank=True)), ('city', models.CharField(blank=True, db_index=True, max_length=100)), ('latitude', models.FloatField(blank=True, null=True)), ('longitude', models.FloatField(blank=True, null=True)), ('visibility', models.CharField(choices=[('public', 'Public'), ('private', 'Private')], db_index=True, default='public', max_length=10)), ('avatar', models.ImageField(blank=True, null=True, upload_to='communities/avatars/')), ('cover_image', models.ImageField(blank=True, null=True, upload_to='communities/covers/')), ('member_count', models.PositiveIntegerField(default=0)), ('is_active', models.BooleanField(db_index=True, default=True)),
            ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='owned_communities', to=settings.AUTH_USER_MODEL)), ('sport', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='communities', to='my_app.sport')),
        ]),
        migrations.CreateModel(name='CommunityMember', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('role', models.CharField(choices=[('owner','Owner'),('admin','Admin'),('moderator','Moderator'),('member','Member')], db_index=True, default='member', max_length=12)), ('status', models.CharField(choices=[('active','Active'),('pending','Pending'),('banned','Banned'),('left','Left')], db_index=True, default='active', max_length=12)), ('joined_at', models.DateTimeField(blank=True, null=True)),
            ('community', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='my_app.community')), ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='community_memberships', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.CreateModel(name='CommunityPost', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('content', models.TextField(blank=True)), ('post_type', models.CharField(choices=[('text','Text'),('game','Game'),('event','Event'),('announcement','Announcement')], db_index=True, default='text', max_length=16)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
            ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='community_posts', to=settings.AUTH_USER_MODEL)), ('community', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='posts', to='my_app.community')), ('game', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='community_posts', to='my_app.game')),
        ]),
        migrations.CreateModel(name='CommunityComment', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)), ('content', models.TextField(blank=True)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
            ('author', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='community_comments', to=settings.AUTH_USER_MODEL)), ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='my_app.communitypost')),
        ]),
        migrations.CreateModel(name='CommunityPostLike', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')), ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)), ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
            ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='likes', to='my_app.communitypost')), ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='community_post_likes', to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddIndex(model_name='community', index=models.Index(fields=['sport','city','is_active'], name='community_discovery_idx')),
        migrations.AddIndex(model_name='community', index=models.Index(fields=['owner','is_active'], name='community_owner_active_idx')),
        migrations.AddConstraint(model_name='community', constraint=models.CheckConstraint(check=models.Q(('latitude__isnull', True)) | models.Q(('latitude__gte', -90), ('latitude__lte', 90)), name='community_latitude_valid')),
        migrations.AddConstraint(model_name='community', constraint=models.CheckConstraint(check=models.Q(('longitude__isnull', True)) | models.Q(('longitude__gte', -180), ('longitude__lte', 180)), name='community_longitude_valid')),
        migrations.AddIndex(model_name='communitymember', index=models.Index(fields=['community','status','role'], name='community_member_status_idx')),
        migrations.AddIndex(model_name='communitymember', index=models.Index(fields=['user','status'], name='community_user_status_idx')),
        migrations.AddConstraint(model_name='communitymember', constraint=models.UniqueConstraint(fields=('community','user'), name='unique_community_member')),
        migrations.AddIndex(model_name='communitypost', index=models.Index(fields=['community','created_at'], name='community_post_created_idx')),
        migrations.AddIndex(model_name='communitycomment', index=models.Index(fields=['post','created_at'], name='community_comment_created_idx')),
        migrations.AddIndex(model_name='communitypostlike', index=models.Index(fields=['post','created_at'], name='community_like_post_idx')),
        migrations.AddConstraint(model_name='communitypostlike', constraint=models.UniqueConstraint(fields=('post','user'), name='unique_community_post_like')),
    ]

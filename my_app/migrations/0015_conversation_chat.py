# Additive Phase 11 conversation chat migration; excludes unrelated legacy drift.
import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('my_app', '0014_game_gameplayer')]

    operations = [
        migrations.CreateModel(
            name='Conversation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('conversation_type', models.CharField(choices=[('one_to_one', 'One to one'), ('game_group', 'Game group')], db_index=True, max_length=16)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('game', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='conversation', to='my_app.game')),
                ('participant_one', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='conversations_as_first_participant', to=settings.AUTH_USER_MODEL)),
                ('participant_two', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='conversations_as_second_participant', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ConversationMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('left_at', models.DateTimeField(blank=True, null=True)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('last_read_at', models.DateTimeField(blank=True, null=True)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='my_app.conversation')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conversation_memberships', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RemoveConstraint(model_name='message', name='message_sender_differs_from_receiver'),
        migrations.AddField(model_name='message', name='conversation', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='my_app.conversation')),
        migrations.AddField(model_name='message', name='deleted_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='message', name='is_edited', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='message', name='message_type', field=models.CharField(choices=[('text', 'Text'), ('system', 'System')], db_index=True, default='text', max_length=10)),
        migrations.AlterField(model_name='message', name='content', field=models.TextField(blank=True)),
        migrations.AlterField(model_name='message', name='receiver', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='received_messages', to=settings.AUTH_USER_MODEL)),
        migrations.AddIndex(model_name='conversation', index=models.Index(fields=['conversation_type', 'active'], name='conversation_type_active_idx')),
        migrations.AddConstraint(model_name='conversation', constraint=models.UniqueConstraint(condition=models.Q(('active', True), ('conversation_type', 'one_to_one')), fields=('participant_one', 'participant_two'), name='unique_active_direct_conversation')),
        migrations.AddConstraint(model_name='conversation', constraint=models.CheckConstraint(check=~models.Q(('participant_one', models.F('participant_two'))), name='conversation_participants_differ')),
        migrations.AddIndex(model_name='conversationmember', index=models.Index(fields=['conversation', 'is_active'], name='conversation_member_active_idx')),
        migrations.AddIndex(model_name='conversationmember', index=models.Index(fields=['user', 'is_active'], name='conversation_user_active_idx')),
        migrations.AddConstraint(model_name='conversationmember', constraint=models.UniqueConstraint(condition=models.Q(('is_active', True)), fields=('conversation', 'user'), name='unique_active_conversation_member')),
        migrations.AddIndex(model_name='message', index=models.Index(fields=['conversation', 'created_at'], name='message_conversation_time_idx')),
        migrations.AddIndex(model_name='message', index=models.Index(fields=['sender', 'created_at'], name='message_sender_time_idx')),
        migrations.AddConstraint(model_name='message', constraint=models.CheckConstraint(check=models.Q(('receiver__isnull', True)) | ~models.Q(('sender', models.F('receiver'))), name='message_sender_differs_from_receiver')),
    ]

"""Conversation, membership and message rules for the SportMate chat API."""
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from my_app.models import Conversation, ConversationMember, Game, GamePlayer, Message, User, UserBlock
from my_app.utils import create_notification


class ChatError(APIException):
    status_code = 400
    default_code = 'CHAT_ERROR'
    default_detail = 'The chat request could not be completed.'


class ChatPermissionService:
    @staticmethod
    def active_member(*, conversation, user):
        try:
            return ConversationMember.objects.get(conversation=conversation, user=user, is_active=True)
        except ConversationMember.DoesNotExist as exc:
            raise PermissionDenied('You do not have access to this conversation.') from exc

    @staticmethod
    def ensure_not_blocked(*, user, other_user):
        if UserBlock.objects.filter(
            Q(user=user, blocked_user=other_user) | Q(user=other_user, blocked_user=user),
        ).exists():
            raise PermissionDenied('Messaging is unavailable between these users.')


class ConversationMemberService:
    @classmethod
    def activate(cls, *, conversation, user):
        member = ConversationMember.objects.select_for_update().filter(
            conversation=conversation, user=user,
        ).order_by('-created_at').first()
        if member:
            if not member.is_active:
                member.is_active = True
                member.left_at = None
                member.save(update_fields=['is_active', 'left_at', 'updated_at'])
            return member
        return ConversationMember.objects.create(conversation=conversation, user=user)

    @classmethod
    def deactivate(cls, *, conversation, user):
        member = ConversationMember.objects.select_for_update().filter(
            conversation=conversation, user=user, is_active=True,
        ).first()
        if member:
            member.is_active = False
            member.left_at = timezone.now()
            member.save(update_fields=['is_active', 'left_at', 'updated_at'])
        return member


class ConversationService:
    @classmethod
    @transaction.atomic
    def one_to_one(cls, *, initiator, target_user_id):
        try:
            target = User.objects.get(pk=target_user_id, is_active=True, account_status=User.AccountStatus.ACTIVE)
        except User.DoesNotExist as exc:
            raise NotFound('Target user not found.') from exc
        if initiator.pk == target.pk:
            raise ValidationError({'user_id': ['You cannot start a conversation with yourself.']})
        ChatPermissionService.ensure_not_blocked(user=initiator, other_user=target)
        first, second = sorted((initiator, target), key=lambda user: user.pk)
        conversation = Conversation.objects.select_for_update().filter(
            conversation_type=Conversation.Type.ONE_TO_ONE, active=True,
            participant_one=first, participant_two=second,
        ).first()
        created = conversation is None
        if created:
            conversation = Conversation.objects.create(
                conversation_type=Conversation.Type.ONE_TO_ONE,
                participant_one=first, participant_two=second,
            )
        ConversationMemberService.activate(conversation=conversation, user=initiator)
        ConversationMemberService.activate(conversation=conversation, user=target)
        return conversation, created

    @classmethod
    @transaction.atomic
    def game_conversation(cls, *, game):
        conversation, _ = Conversation.objects.get_or_create(
            game=game,
            defaults={'conversation_type': Conversation.Type.GAME_GROUP},
        )
        if not conversation.active:
            conversation.active = True
            conversation.save(update_fields=['active', 'updated_at'])
        for game_player in game.game_players.filter(
            status=GamePlayer.Status.CONFIRMED,
        ).select_related('user'):
            ConversationMemberService.activate(conversation=conversation, user=game_player.user)
        return conversation

    @classmethod
    @transaction.atomic
    def game_for_user(cls, *, game_id, user):
        try:
            game = Game.objects.select_for_update().get(public_id=game_id)
        except Game.DoesNotExist as exc:
            raise NotFound('Game not found.') from exc
        if not GamePlayer.objects.filter(game=game, user=user, status=GamePlayer.Status.CONFIRMED).exists():
            raise PermissionDenied('Only confirmed game players can access this chat.')
        return cls.game_conversation(game=game)

    @classmethod
    @transaction.atomic
    def sync_game_member(cls, *, game, user, active):
        conversation = cls.game_conversation(game=game)
        if active:
            ConversationMemberService.activate(conversation=conversation, user=user)
        else:
            ConversationMemberService.deactivate(conversation=conversation, user=user)
        return conversation


class MessageService:
    @staticmethod
    def _content(content):
        text = (content or '').strip()
        if not text:
            raise ValidationError({'content': ['Message content cannot be empty.']})
        if len(text) > settings.CHAT_MESSAGE_MAX_LENGTH:
            raise ValidationError({'content': [f'Message content cannot exceed {settings.CHAT_MESSAGE_MAX_LENGTH} characters.']})
        return text

    @classmethod
    @transaction.atomic
    def send(cls, *, conversation, sender, content):
        if not conversation.active:
            raise ChatError('This conversation is inactive.')
        ChatPermissionService.active_member(conversation=conversation, user=sender)
        if conversation.conversation_type == Conversation.Type.ONE_TO_ONE:
            other = conversation.participant_two if conversation.participant_one_id == sender.id else conversation.participant_one
            ChatPermissionService.ensure_not_blocked(user=sender, other_user=other)
        message = Message.objects.create(
            conversation=conversation, sender=sender, content=cls._content(content), message_type=Message.Type.TEXT,
        )
        recipients = ConversationMember.objects.filter(conversation=conversation, is_active=True).exclude(user=sender).select_related('user')
        for member in recipients:
            create_notification(
                user=member.user, type='message', title=f'New message from {sender.username}',
                body=message.content[:80], data={'message_id': message.public_id.hex, 'conversation_id': conversation.public_id.hex},
            )
        return message

    @classmethod
    def system_message(cls, *, conversation, content):
        # System messages are only called by backend services and never exposed
        # through a client-controlled message type.
        sender = conversation.game.host if conversation.game_id else conversation.participant_one
        return Message.objects.create(
            conversation=conversation, sender=sender, content=content, message_type=Message.Type.SYSTEM,
        )

    @classmethod
    @transaction.atomic
    def edit(cls, *, message, actor, content):
        if message.sender_id != actor.id:
            raise PermissionDenied('Only the sender may edit this message.')
        if message.deleted_at:
            raise ChatError('Deleted messages cannot be edited.')
        if message.message_type != Message.Type.TEXT:
            raise ChatError('System messages cannot be edited.')
        message.content = cls._content(content)
        message.is_edited = True
        message.save(update_fields=['content', 'is_edited', 'updated_at'])
        return message

    @classmethod
    @transaction.atomic
    def delete(cls, *, message, actor):
        if message.sender_id != actor.id and not actor.is_staff:
            raise PermissionDenied('Only the sender may delete this message.')
        if not message.deleted_at:
            message.deleted_at = timezone.now()
            message.content = ''
            message.save(update_fields=['deleted_at', 'content', 'updated_at'])
        return message


class UnreadMessageService:
    @staticmethod
    @transaction.atomic
    def mark_read(*, conversation, user):
        member = ChatPermissionService.active_member(conversation=conversation, user=user)
        member.last_read_at = timezone.now()
        member.save(update_fields=['last_read_at', 'updated_at'])
        return member

"""Transactional community membership, content, moderation and discovery rules."""
from django.db import models, transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from my_app.models import Community, CommunityComment, CommunityMember, CommunityPost, CommunityPostLike, Game
from my_app.services.locations import nearby_queryset
from my_app.utils import create_notification


class CommunityError(APIException):
    status_code = 400
    default_code = 'COMMUNITY_ERROR'
    default_detail = 'The community request could not be completed.'


class CommunityMembershipService:
    @staticmethod
    def active_member(*, community, user):
        try:
            return CommunityMember.objects.get(community=community, user=user, status=CommunityMember.Status.ACTIVE)
        except CommunityMember.DoesNotExist as exc:
            raise PermissionDenied('You must be an active community member.') from exc

    @staticmethod
    def moderator(*, community, user):
        member = CommunityMembershipService.active_member(community=community, user=user)
        if member.role not in (CommunityMember.Role.OWNER, CommunityMember.Role.ADMIN, CommunityMember.Role.MODERATOR):
            raise PermissionDenied('Community moderator permission is required.')
        return member

    @staticmethod
    def admin(*, community, user):
        member = CommunityMembershipService.active_member(community=community, user=user)
        if member.role not in (CommunityMember.Role.OWNER, CommunityMember.Role.ADMIN):
            raise PermissionDenied('Community administrator permission is required.')
        return member

    @staticmethod
    def recalculate_count(community):
        count = CommunityMember.objects.filter(community=community, status=CommunityMember.Status.ACTIVE).count()
        Community.objects.filter(pk=community.pk).update(member_count=count, updated_at=timezone.now())
        community.member_count = count
        return count


class CommunityService:
    @classmethod
    @transaction.atomic
    def create(cls, *, owner, name, description='', sport=None, city='', latitude=None, longitude=None, visibility='public'):
        base = slugify(name)[:160] or 'community'
        slug = base
        suffix = 2
        while Community.objects.filter(slug=slug).exists():
            ending = f'-{suffix}'
            slug = f'{base[:170 - len(ending)]}{ending}'
            suffix += 1
        community = Community.objects.create(
            name=name.strip(), slug=slug, description=description.strip(), owner=owner, sport=sport,
            city=city.strip(), latitude=latitude, longitude=longitude, visibility=visibility,
        )
        CommunityMember.objects.create(community=community, user=owner, role=CommunityMember.Role.OWNER, status=CommunityMember.Status.ACTIVE, joined_at=timezone.now())
        CommunityMembershipService.recalculate_count(community)
        return community

    @classmethod
    @transaction.atomic
    def join(cls, *, community, user):
        if not community.is_active:
            raise CommunityError('This community is inactive.')
        member = CommunityMember.objects.select_for_update().filter(community=community, user=user).first()
        if member and member.status == CommunityMember.Status.BANNED:
            raise PermissionDenied('You are banned from this community.')
        if member and member.status in (CommunityMember.Status.ACTIVE, CommunityMember.Status.PENDING):
            return member, False
        target_status = CommunityMember.Status.ACTIVE if community.visibility == Community.Visibility.PUBLIC else CommunityMember.Status.PENDING
        if member:
            member.status, member.role, member.joined_at = target_status, CommunityMember.Role.MEMBER, (timezone.now() if target_status == CommunityMember.Status.ACTIVE else None)
            member.save(update_fields=['status', 'role', 'joined_at', 'updated_at'])
        else:
            member = CommunityMember.objects.create(community=community, user=user, status=target_status, joined_at=(timezone.now() if target_status == CommunityMember.Status.ACTIVE else None))
        if target_status == CommunityMember.Status.ACTIVE:
            CommunityMembershipService.recalculate_count(community)
        else:
            create_notification(user=community.owner, type='message', title='Community join request', body=f'{user.username} requested to join {community.name}.', data={'community_id': community.public_id.hex, 'user_id': user.id})
        return member, True

    @classmethod
    @transaction.atomic
    def leave(cls, *, community, user):
        member = CommunityMembershipService.active_member(community=community, user=user)
        if member.role == CommunityMember.Role.OWNER:
            raise CommunityError('The owner cannot leave without transferring ownership.')
        member.status = CommunityMember.Status.LEFT
        member.save(update_fields=['status', 'updated_at'])
        CommunityMembershipService.recalculate_count(community)
        return member

    @classmethod
    @transaction.atomic
    def decide_request(cls, *, community, actor, user_id, approve):
        CommunityMembershipService.admin(community=community, user=actor)
        try:
            member = CommunityMember.objects.select_for_update().select_related('user').get(community=community, user_id=user_id, status=CommunityMember.Status.PENDING)
        except CommunityMember.DoesNotExist as exc:
            raise NotFound('Pending membership request not found.') from exc
        member.status = CommunityMember.Status.ACTIVE if approve else CommunityMember.Status.LEFT
        member.joined_at = timezone.now() if approve else None
        member.save(update_fields=['status', 'joined_at', 'updated_at'])
        if approve:
            CommunityMembershipService.recalculate_count(community)
        create_notification(user=member.user, type='message', title='Community request updated', body=(f'You joined {community.name}.' if approve else f'Your request to join {community.name} was declined.'), data={'community_id': community.public_id.hex})
        return member


class CommunityPostService:
    @staticmethod
    def content(value):
        value = (value or '').strip()
        if not value:
            raise ValidationError({'content': ['Content cannot be empty.']})
        if len(value) > 5000:
            raise ValidationError({'content': ['Content cannot exceed 5000 characters.']})
        return value

    @classmethod
    @transaction.atomic
    def create(cls, *, community, author, content, post_type='text', game_id=None):
        member = CommunityMembershipService.active_member(community=community, user=author)
        if post_type == CommunityPost.Type.ANNOUNCEMENT and member.role not in (CommunityMember.Role.OWNER, CommunityMember.Role.ADMIN, CommunityMember.Role.MODERATOR):
            raise PermissionDenied('Only moderators can create announcements.')
        game = None
        if game_id:
            try:
                game = Game.objects.get(public_id=game_id)
            except Game.DoesNotExist as exc:
                raise NotFound('Game not found.') from exc
        post = CommunityPost.objects.create(community=community, author=author, content=cls.content(content), post_type=post_type, game=game)
        if post_type == CommunityPost.Type.ANNOUNCEMENT:
            for membership in community.members.filter(status=CommunityMember.Status.ACTIVE).exclude(user=author).select_related('user'):
                create_notification(user=membership.user, type='message', title=f'Announcement in {community.name}', body=post.content[:80], data={'community_post_id': post.public_id.hex})
        return post

    @classmethod
    @transaction.atomic
    def edit(cls, *, post, actor, content):
        member = CommunityMembershipService.active_member(community=post.community, user=actor)
        if post.author_id != actor.id and member.role not in (CommunityMember.Role.OWNER, CommunityMember.Role.ADMIN, CommunityMember.Role.MODERATOR):
            raise PermissionDenied('You cannot edit this post.')
        if post.deleted_at:
            raise CommunityError('Deleted posts cannot be edited.')
        post.content = cls.content(content)
        post.save(update_fields=['content', 'updated_at'])
        return post

    @classmethod
    @transaction.atomic
    def delete(cls, *, post, actor):
        member = CommunityMembershipService.active_member(community=post.community, user=actor)
        if post.author_id != actor.id and member.role not in (CommunityMember.Role.OWNER, CommunityMember.Role.ADMIN, CommunityMember.Role.MODERATOR):
            raise PermissionDenied('You cannot delete this post.')
        post.deleted_at, post.content = timezone.now(), ''
        post.save(update_fields=['deleted_at', 'content', 'updated_at'])
        return post


class CommunityCommentService:
    @classmethod
    def create(cls, *, post, author, content):
        if post.deleted_at:
            raise CommunityError('Comments cannot be added to a deleted post.')
        CommunityMembershipService.active_member(community=post.community, user=author)
        return CommunityComment.objects.create(post=post, author=author, content=CommunityPostService.content(content))

    @classmethod
    def change(cls, *, comment, actor, content=None, delete=False):
        member = CommunityMembershipService.active_member(community=comment.post.community, user=actor)
        if comment.author_id != actor.id and member.role not in (CommunityMember.Role.OWNER, CommunityMember.Role.ADMIN, CommunityMember.Role.MODERATOR):
            raise PermissionDenied('You cannot moderate this comment.')
        if delete:
            comment.deleted_at, comment.content = timezone.now(), ''
            comment.save(update_fields=['deleted_at', 'content', 'updated_at'])
        else:
            if comment.deleted_at:
                raise CommunityError('Deleted comments cannot be edited.')
            comment.content = CommunityPostService.content(content)
            comment.save(update_fields=['content', 'updated_at'])
        return comment


class CommunityModerationService:
    @classmethod
    @transaction.atomic
    def member_action(cls, *, community, actor, user_id, ban=False):
        actor_member = CommunityMembershipService.moderator(community=community, user=actor)
        member = CommunityMember.objects.select_for_update().select_related('user').get(community=community, user_id=user_id)
        if member.role == CommunityMember.Role.OWNER and actor_member.role != CommunityMember.Role.OWNER:
            raise PermissionDenied('Only the owner can manage the owner membership.')
        if member.role in (CommunityMember.Role.ADMIN, CommunityMember.Role.MODERATOR) and actor_member.role == CommunityMember.Role.MODERATOR:
            raise PermissionDenied('Moderators cannot manage administrators or moderators.')
        member.status = CommunityMember.Status.BANNED if ban else CommunityMember.Status.LEFT
        member.save(update_fields=['status', 'updated_at'])
        CommunityMembershipService.recalculate_count(community)
        create_notification(user=member.user, type='message', title=f'Community membership updated', body=(f'You were banned from {community.name}.' if ban else f'You were removed from {community.name}.'), data={'community_id': community.public_id.hex})
        return member


class CommunityDiscoveryService:
    WEIGHTS = {'sport': 35, 'distance': 25, 'activity': 20, 'member': 10, 'quality': 10}

    @classmethod
    def discover(cls, *, user, queryset, latitude=None, longitude=None, radius=None):
        communities = queryset.filter(is_active=True).select_related('sport', 'owner').annotate(
            active_members=Count('members', filter=models.Q(members__status=CommunityMember.Status.ACTIVE), distinct=True),
            recent_posts=Count('posts', distinct=True),
        )
        if latitude is not None:
            communities = nearby_queryset(communities, latitude=latitude, longitude=longitude, radius=radius, latitude_field='latitude', longitude_field='longitude')
        sports = {item.sport_id: item for item in user.user_sports.all()}
        output = []
        for community in communities[:500]:
            sport_entry = sports.get(community.sport_id)
            sport_score = 35 if sport_entry and sport_entry.preferred else 25 if sport_entry else 0
            distance = getattr(community, 'distance_km', None)
            distance_score = 0 if distance is None else next((score for threshold, score in ((2,25),(5,21),(10,15),(20,8)) if distance <= threshold), 0)
            activity = 20 if community.recent_posts >= 5 else 12 if community.recent_posts else 4
            member = 10 if community.city and community.city.lower() == (user.city or '').lower() else 5 if community.active_members else 0
            quality = 10 if community.active_members >= 10 else 6 if community.active_members else 2
            community.discovery_score = sport_score + distance_score + activity + member + quality
            output.append(community)
        return sorted(output, key=lambda item: (-item.discovery_score, getattr(item, 'distance_km', float('inf')), -item.active_members))

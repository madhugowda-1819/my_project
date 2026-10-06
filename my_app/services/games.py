"""Transactional business rules for court-backed SportMate games.

The service deliberately owns all capacity, schedule and lifecycle decisions;
views only parse HTTP requests and serialize results.
"""
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from my_app.models import Court, Game, GamePlayer, Sport, UserSport, Venue
from my_app.services.bookings import BookingService


ACTIVE_GAME_STATUSES = (
    Game.Status.OPEN,
    Game.Status.ALMOST_FULL,
    Game.Status.FULL,
    Game.Status.STARTED,
)
JOINABLE_GAME_STATUSES = (Game.Status.OPEN, Game.Status.ALMOST_FULL)


class GameError(APIException):
    status_code = 400
    default_code = 'GAME_ERROR'
    default_detail = 'The game request could not be completed.'


class GameUnavailable(GameError):
    status_code = 409
    default_code = 'GAME_UNAVAILABLE'
    default_detail = 'This game is no longer available.'


class GameConflict(GameError):
    status_code = 409
    default_code = 'GAME_SLOT_UNAVAILABLE'
    default_detail = 'The selected court slot is no longer available.'


class GameLifecycleService:
    @classmethod
    def refresh(cls, game, *, now=None, save=True):
        """Derive lifecycle state from time and confirmed capacity."""
        now = now or timezone.now()
        if game.status == Game.Status.CANCELLED:
            return game
        if game.ends_at <= now:
            target = Game.Status.COMPLETED
        elif game.starts_at <= now:
            target = Game.Status.STARTED
        else:
            confirmed = game.game_players.filter(status=GamePlayer.Status.CONFIRMED).count()
            if confirmed >= game.max_players:
                target = Game.Status.FULL
            elif game.max_players - confirmed <= settings.GAME_ALMOST_FULL_SLOTS_THRESHOLD:
                target = Game.Status.ALMOST_FULL
            else:
                target = Game.Status.OPEN
        if save and game.status != target:
            game.status = target
            game.save(update_fields=['status', 'updated_at'])
        else:
            game.status = target
        return game


class GameService:
    @staticmethod
    def _reference():
        return f'GM-{secrets.token_urlsafe(6).upper().replace("_", "X").replace("-", "Y")}'[:20]

    @staticmethod
    def _require_sport_eligibility(*, user, sport, required_skill=''):
        try:
            user_sport = UserSport.objects.get(user=user, sport=sport)
        except UserSport.DoesNotExist as exc:
            raise GameError('A player must add this sport to their profile before joining.') from exc
        if not required_skill:
            return
        levels = [choice for choice, _ in UserSport.SkillLevel.choices]
        difference = abs(levels.index(user_sport.skill_level) - levels.index(required_skill))
        if difference > settings.GAME_MAX_SKILL_LEVEL_DIFFERENCE:
            raise GameError('Your skill level does not meet this game requirement.')

    @classmethod
    def _validate_slot(cls, *, venue, court, game_date, start_time, end_time, exclude_game_id=None):
        # This is the same authoritative validation used by bookings: venue
        # status, court ownership/activity, local timezone, hours, durations,
        # blocked periods and confirmed/pending booking overlaps.
        starts_at, ends_at = BookingService._validate_request(
            venue=venue, court=court, booking_date=game_date,
            start_time=start_time, end_time=end_time, lock_bookings=True,
        )
        if starts_at <= timezone.now():
            raise GameError('Games must be scheduled in the future.')
        conflicts = Game.objects.filter(
            court=court, status__in=ACTIVE_GAME_STATUSES,
            starts_at__lt=ends_at, ends_at__gt=starts_at,
        )
        if exclude_game_id:
            conflicts = conflicts.exclude(pk=exclude_game_id)
        if conflicts.select_for_update().exists():
            raise GameConflict()
        return starts_at, ends_at

    @classmethod
    @transaction.atomic
    def create(cls, *, host, sport_id, venue_id, court_id, game_date, start_time, end_time,
               min_players, max_players, skill_level='', description='', visibility=Game.Visibility.PUBLIC):
        try:
            sport = Sport.objects.get(pk=sport_id, is_active=True)
        except Sport.DoesNotExist as exc:
            raise NotFound('Sport not found or inactive.') from exc
        try:
            venue = Venue.objects.select_for_update().get(public_id=venue_id)
        except Venue.DoesNotExist as exc:
            raise NotFound('Venue not found.') from exc
        try:
            court = Court.objects.select_for_update().get(public_id=court_id)
        except Court.DoesNotExist as exc:
            raise NotFound('Court not found.') from exc
        if court.sport_id != sport.id:
            raise GameError('The selected court does not support this sport.')
        if min_players > max_players:
            raise ValidationError({'min_players': ['min_players cannot exceed max_players.']})
        if max_players > court.capacity:
            raise ValidationError({'max_players': ['max_players cannot exceed court capacity.']})
        cls._require_sport_eligibility(user=host, sport=sport, required_skill=skill_level)
        starts_at, ends_at = cls._validate_slot(
            venue=venue, court=court, game_date=game_date, start_time=start_time, end_time=end_time,
        )
        for _ in range(5):
            try:
                game = Game.objects.create(
                    game_reference=cls._reference(), host=host, sport=sport, venue=venue, court=court,
                    game_date=game_date, start_time=start_time, end_time=end_time,
                    starts_at=starts_at, ends_at=ends_at, min_players=min_players,
                    max_players=max_players, skill_level=skill_level, description=description.strip(),
                    visibility=visibility,
                )
                GamePlayer.objects.create(game=game, user=host)
                from my_app.services.chat import ConversationService, MessageService
                conversation = ConversationService.sync_game_member(game=game, user=host, active=True)
                MessageService.system_message(conversation=conversation, content=f'{host.username} created the game.')
                return GameLifecycleService.refresh(game)
            except IntegrityError:
                # Only expected for the short random reference collision; any
                # membership collision cannot happen before the game exists.
                continue
        raise GameError('Could not allocate a unique game reference.')

    @classmethod
    @transaction.atomic
    def join(cls, *, game_id, user):
        try:
            game = Game.objects.select_for_update().select_related('sport').get(public_id=game_id)
        except Game.DoesNotExist as exc:
            raise NotFound('Game not found.') from exc
        GameLifecycleService.refresh(game)
        if game.visibility == Game.Visibility.PRIVATE and game.host_id != user.id:
            raise PermissionDenied('This is a private game.')
        if game.status not in JOINABLE_GAME_STATUSES:
            raise GameUnavailable('Only open games can be joined.')
        membership = GamePlayer.objects.select_for_update().filter(game=game, user=user).order_by('-created_at').first()
        if membership and membership.status == GamePlayer.Status.CONFIRMED:
            return game, False
        if game.game_players.filter(status=GamePlayer.Status.CONFIRMED).count() >= game.max_players:
            GameLifecycleService.refresh(game)
            raise GameUnavailable('This game is full.')
        cls._require_sport_eligibility(user=user, sport=game.sport, required_skill=game.skill_level)
        if membership:
            membership.status = GamePlayer.Status.CONFIRMED
            membership.left_at = None
            membership.save(update_fields=['status', 'left_at', 'updated_at'])
        else:
            GamePlayer.objects.create(game=game, user=user)
        from my_app.services.chat import ConversationService, MessageService
        conversation = ConversationService.sync_game_member(game=game, user=user, active=True)
        MessageService.system_message(conversation=conversation, content=f'{user.username} joined the game.')
        return GameLifecycleService.refresh(game), True

    @classmethod
    @transaction.atomic
    def leave(cls, *, game_id, user):
        try:
            game = Game.objects.select_for_update().get(public_id=game_id)
        except Game.DoesNotExist as exc:
            raise NotFound('Game not found.') from exc
        GameLifecycleService.refresh(game)
        if game.status == Game.Status.CANCELLED:
            raise GameError('Cancelled games cannot be left.')
        if game.status in (Game.Status.STARTED, Game.Status.COMPLETED):
            raise GameError('Games that have started cannot be left.')
        if game.host_id == user.id:
            # A host cannot silently orphan a game; leaving is an explicit
            # cancellation. Host-transfer can be introduced with invitations.
            game.status = Game.Status.CANCELLED
            game.save(update_fields=['status', 'updated_at'])
            game.game_players.filter(user=user, status=GamePlayer.Status.CONFIRMED).update(
                status=GamePlayer.Status.LEFT, left_at=timezone.now(), updated_at=timezone.now(),
            )
            from my_app.services.chat import ConversationService, MessageService
            conversation = ConversationService.sync_game_member(game=game, user=user, active=False)
            MessageService.system_message(conversation=conversation, content='The host cancelled the game.')
            return game, 'cancelled'
        membership = GamePlayer.objects.select_for_update().filter(
            game=game, user=user, status=GamePlayer.Status.CONFIRMED,
        ).first()
        if not membership:
            raise GameError('You are not an active player in this game.')
        membership.status = GamePlayer.Status.LEFT
        membership.left_at = timezone.now()
        membership.save(update_fields=['status', 'left_at', 'updated_at'])
        from my_app.services.chat import ConversationService, MessageService
        conversation = ConversationService.sync_game_member(game=game, user=user, active=False)
        MessageService.system_message(conversation=conversation, content=f'{user.username} left the game.')
        return GameLifecycleService.refresh(game), 'left'

    @classmethod
    @transaction.atomic
    def update(cls, *, game_id, actor, **changes):
        try:
            game = Game.objects.select_for_update().get(public_id=game_id)
        except Game.DoesNotExist as exc:
            raise NotFound('Game not found.') from exc
        if game.host_id != actor.id and not actor.is_staff:
            raise PermissionDenied('Only the host may update this game.')
        GameLifecycleService.refresh(game)
        if game.status in (Game.Status.CANCELLED, Game.Status.COMPLETED, Game.Status.STARTED):
            raise GameError('This game can no longer be updated.')
        confirmed = game.game_players.filter(status=GamePlayer.Status.CONFIRMED).count()
        if changes.get('max_players', game.max_players) < confirmed:
            raise ValidationError({'max_players': ['max_players cannot be below confirmed players.']})
        if changes.get('min_players', game.min_players) > changes.get('max_players', game.max_players):
            raise ValidationError({'min_players': ['min_players cannot exceed max_players.']})
        if changes.get('max_players', game.max_players) > game.court.capacity:
            raise ValidationError({'max_players': ['max_players cannot exceed court capacity.']})
        for field, value in changes.items():
            setattr(game, field, value)
        game.save(update_fields=[*changes.keys(), 'updated_at'])
        return GameLifecycleService.refresh(game)

    @classmethod
    @transaction.atomic
    def cancel(cls, *, game_id, actor):
        try:
            game = Game.objects.select_for_update().get(public_id=game_id)
        except Game.DoesNotExist as exc:
            raise NotFound('Game not found.') from exc
        if game.host_id != actor.id and not actor.is_staff:
            raise PermissionDenied('Only the host may cancel this game.')
        GameLifecycleService.refresh(game)
        if game.status in (Game.Status.STARTED, Game.Status.COMPLETED):
            raise GameError('Games that have started cannot be cancelled.')
        game.status = Game.Status.CANCELLED
        game.save(update_fields=['status', 'updated_at'])
        # Cancellation is visible to the current group; historical chat is kept.
        from my_app.services.chat import ConversationService, MessageService
        conversation = ConversationService.game_conversation(game=game)
        MessageService.system_message(conversation=conversation, content='The game was cancelled.')
        return game

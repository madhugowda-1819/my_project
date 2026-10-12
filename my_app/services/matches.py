from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from my_app.models import Match
from my_app.utils import create_notification
from my_app.services.notifications import NotificationService


class MatchUnavailable(APIException):
    status_code = 409
    default_code = 'BOOKING_UNAVAILABLE'
    default_detail = 'The selected match is no longer available.'


@transaction.atomic
def create_match(*, organizer, validated_data):
    return Match.objects.create(organizer=organizer, **validated_data)


@transaction.atomic
def join_match(*, match_id, user):
    try:
        match = Match.objects.select_for_update().get(pk=match_id)
    except Match.DoesNotExist as exc:
        raise NotFound('Match not found.') from exc

    if match.status != 'upcoming':
        raise MatchUnavailable('Only upcoming matches can be joined.')
    # A match's scheduled start time is also its registration cut-off.  This
    # must be checked here (rather than only in the app) so an old client or a
    # direct API request cannot join after the match has started.
    if match.date_time <= timezone.now():
        raise MatchUnavailable('This match has expired and can no longer be joined.')
    if match.joined_players.filter(pk=user.pk).exists():
        return match, False
    if match.joined_players.count() >= match.total_players:
        raise MatchUnavailable('This match is full.')

    match.joined_players.add(user)
    create_notification(
        user=match.organizer,
        type='match_invite',
        title='Player joined',
        body=f'{user.username} joined your match.',
        data={'match_id': match.public_id.hex},
    )
    return match, True


def _participants_can_change(match):
    return match.status == 'upcoming' and match.date_time > timezone.now()


@transaction.atomic
def leave_match(*, match_id, user):
    try:
        match = Match.objects.select_for_update().get(pk=match_id)
    except Match.DoesNotExist as exc:
        raise NotFound('Match not found.') from exc
    if not _participants_can_change(match):
        raise MatchUnavailable('Participants can only leave an upcoming match.')
    if not match.joined_players.filter(pk=user.pk).exists():
        raise MatchUnavailable('You are not a participant in this match.')
    match.joined_players.remove(user)
    return match


@transaction.atomic
def remove_player(*, match_id, organizer, player_id, reason=''):
    try:
        match = Match.objects.select_for_update().get(pk=match_id)
    except Match.DoesNotExist as exc:
        raise NotFound('Match not found.') from exc
    if match.organizer_id != organizer.id:
        raise PermissionDenied('Only the match organizer can remove participants.')
    if not _participants_can_change(match):
        raise MatchUnavailable('Participants can only be removed from an upcoming match.')
    if player_id == organizer.id:
        raise MatchUnavailable('The organizer cannot be removed as a participant.')
    try:
        player = match.joined_players.get(pk=player_id)
    except match.joined_players.model.DoesNotExist as exc:
        raise MatchUnavailable('That player is not a participant in this match.') from exc
    match.joined_players.remove(player)
    suffix = f' Reason: {reason.strip()}' if reason.strip() else ''
    NotificationService.create_notification(
        user=player, actor=organizer, type='match_invite', title='Removed from match',
        body=f'You were removed from {match.sport.name} at {match.ground.name}.{suffix}',
        data={'match_id': match.public_id.hex, 'reason': reason.strip()},
        event_key=f'match-removed:{match.pk}:{player.pk}',
    )
    return match

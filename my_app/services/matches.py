from django.db import transaction
from rest_framework.exceptions import APIException, NotFound

from my_app.models import Match, Notification


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
    if match.joined_players.filter(pk=user.pk).exists():
        return match, False
    if match.joined_players.count() >= match.total_players:
        raise MatchUnavailable('This match is full.')

    match.joined_players.add(user)
    Notification.objects.create(
        user=match.organizer,
        type='match_invite',
        title='Player joined',
        body=f'{user.username} joined your match.',
        data={'match_id': match.public_id.hex},
    )
    return match, True

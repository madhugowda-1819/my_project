from django.db import transaction
from django.conf import settings
from django.db.models import F, Q
from datetime import timedelta
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied

from my_app.models import (AvailabilitySlot, Match, MatchParticipation, MatchReplacementHistory,
                           MatchReplacementInvitation, MatchReplacementOpportunity,
                           User, UserBlock, UserSport, SportFormat)
from my_app.utils import create_notification
from my_app.services.notifications import NotificationService
from my_app.services.locations import nearby_queryset


class MatchUnavailable(APIException):
    status_code = 409
    default_code = 'BOOKING_UNAVAILABLE'
    default_detail = 'The selected match is no longer available.'

def sport_match_capacity(sport):
    configured = SportFormat.objects.filter(sport=sport, is_active=True).order_by('id').first()
    return configured.maximum_players if configured else None


def _close_full_match_rescues(match, now=None):
    now = now or timezone.now()
    open_opportunities = match.replacement_opportunities.filter(closed_at__isnull=True)
    if not open_opportunities.exists():
        return
    open_opportunities.update(closed_at=now, updated_at=now)
    MatchReplacementInvitation.objects.filter(
        opportunity__match=match, status=MatchReplacementInvitation.Status.PENDING,
    ).update(status=MatchReplacementInvitation.Status.UNAVAILABLE, responded_at=now, updated_at=now)


@transaction.atomic
def create_match(*, organizer, validated_data):
    match = Match.objects.create(organizer=organizer, **validated_data)
    match.joined_players.add(organizer)
    MatchParticipation.objects.create(match=match, user=organizer)
    return match


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
    MatchParticipation.objects.create(match=match, user=user)
    participation = MatchParticipation.objects.filter(match=match, user=user, status=MatchParticipation.Status.JOINED).order_by('-created_at', '-pk').first()
    if match.joined_players.count() >= match.total_players:
        _close_full_match_rescues(match)
    NotificationService.create_notification(
        user=match.organizer,
        actor=user,
        type='match_invite',
        title='Player joined',
        body=f'{user.username} joined your match.',
        data={'match_id': str(match.public_id)},
        event_key=f'match-joined:{match.pk}:{participation.pk if participation else user.pk}',
    )
    return match, True


def _participants_can_change(match):
    return match.status == 'upcoming' and match.date_time > timezone.now()


def _candidate_available(candidate, starts_at):
    """A configured weekly slot must cover the match start; no slots means no restriction."""
    slots = AvailabilitySlot.objects.filter(user=candidate, day_of_week=starts_at.weekday())
    if not slots.exists():
        profile = getattr(candidate, 'profile', None)
        preferred = (profile.preferred_play_times or []) if profile else []
        bucket = 'morning' if starts_at.hour < 12 else ('afternoon' if starts_at.hour < 17 else 'evening')
        return not preferred or bucket in {str(value).lower() for value in preferred} or 'any' in preferred
    local_time = timezone.localtime(starts_at).time()
    return slots.filter(start_time__lte=local_time, end_time__gt=local_time).exists()


def _candidate_is_eligible(candidate, match, *, withdrawn_user_id=None):
    if withdrawn_user_id is not None and candidate.pk == withdrawn_user_id:
        return False
    if (not candidate.is_active or candidate.account_status != User.AccountStatus.ACTIVE
            or not candidate.is_available or match.joined_players.filter(pk=candidate.pk).exists()
            or match.organizer_id == candidate.pk):
        return False
    if not UserSport.objects.filter(user=candidate, sport=match.sport).exists():
        return False
    participants = list(match.joined_players.values_list('pk', flat=True)) + [match.organizer_id]
    if withdrawn_user_id is not None:
        participants.append(withdrawn_user_id)
    blocked = UserBlock.objects.filter(
        Q(user=candidate, blocked_user_id__in=participants)
        | Q(blocked_user=candidate, user_id__in=participants)
    ).exists()
    if blocked:
        return False
    if match.ground.latitude is not None and match.ground.longitude is not None:
        if candidate.latitude is None or candidate.longitude is None:
            return False
        distance = match.ground.distance_to(candidate.latitude, candidate.longitude)
        if distance > float(getattr(settings, 'MATCH_RESCUE_RADIUS_KM', 30)):
            return False
    return _candidate_available(candidate, match.date_time)


def _notify_rescue_candidates(match, withdrawn_user):
    now = timezone.now()
    rescue_window = max(1, int(getattr(settings, 'MATCH_RESCUE_WINDOW_MINUTES', 360)))
    if not (now < match.date_time <= now + timedelta(minutes=rescue_window)):
        return None
    ttl = max(1, int(getattr(settings, 'MATCH_RESCUE_INVITATION_TTL_MINUTES', 120)))
    expires_at = min(match.date_time, now + timedelta(minutes=ttl))
    opportunity = MatchReplacementOpportunity.objects.create(
        match=match, withdrawn_user=withdrawn_user, expires_at=expires_at,
    )
    participant_ids = list(match.joined_players.values_list('pk', flat=True)) + [match.organizer_id, withdrawn_user.pk]
    blocked_ids = set(UserBlock.objects.filter(
        Q(user__in=participant_ids) | Q(blocked_user__in=participant_ids)
    ).values_list('user_id', flat=True)) | set(UserBlock.objects.filter(
        Q(user__in=participant_ids) | Q(blocked_user__in=participant_ids)
    ).values_list('blocked_user_id', flat=True))
    candidates = User.objects.filter(
        is_active=True, account_status=User.AccountStatus.ACTIVE, is_available=True,
        profile__profile_visibility='public', user_sports__sport=match.sport,
    ).exclude(pk__in=participant_ids).exclude(pk__in=blocked_ids).select_related('profile').distinct()
    ground = match.ground
    radius = max(1, min(500, float(getattr(settings, 'MATCH_RESCUE_RADIUS_KM', 30))))
    if ground.latitude is not None and ground.longitude is not None:
        candidates = nearby_queryset(candidates, latitude=ground.latitude, longitude=ground.longitude, radius=radius)
    candidates = list(candidates[:max(1, int(getattr(settings, 'MATCH_RESCUE_MAX_INVITATIONS', 20)))])
    for candidate in candidates:
        if not _candidate_is_eligible(candidate, match, withdrawn_user_id=withdrawn_user.pk):
            continue
        invitation = MatchReplacementInvitation.objects.create(opportunity=opportunity, candidate=candidate)
        NotificationService.create_notification(
            user=candidate, actor=withdrawn_user, type='match_invite',
            title='A match slot opened nearby',
            body=f'A {match.sport.name} match at {ground.city} needs a player. Open Matches to review and accept.',
            data={'rescue_invitation_id': str(invitation.public_id), 'match_id': str(match.public_id)},
            event_key=f'match-rescue-invite:{invitation.public_id}',
        )
    return opportunity


def rescue_opportunities_for(*, user):
    now = timezone.now()
    stale = MatchReplacementInvitation.objects.filter(
        candidate=user, status=MatchReplacementInvitation.Status.PENDING,
    ).filter(
        Q(opportunity__expires_at__lte=now)
        | Q(opportunity__match__status__in=['cancelled', 'completed', 'ongoing'])
        | Q(opportunity__match__date_time__lte=now)
    )
    stale.update(status=MatchReplacementInvitation.Status.EXPIRED, responded_at=now, updated_at=now)
    invitations = MatchReplacementInvitation.objects.filter(
        candidate=user, status=MatchReplacementInvitation.Status.PENDING,
        opportunity__closed_at__isnull=True, opportunity__expires_at__gt=now,
        opportunity__match__status='upcoming', opportunity__match__date_time__gt=now,
    ).select_related('opportunity__match__sport', 'opportunity__match__ground')
    available = []
    for item in invitations:
        match = item.opportunity.match
        if match.joined_players.count() >= match.total_players:
            _close_full_match_rescues(match, now)
            continue
        if _candidate_is_eligible(user, match, withdrawn_user_id=item.opportunity.withdrawn_user_id):
            available.append(item)
    return available


def accept_rescue_invitation(*, invitation_id, user):
    try:
        preliminary = MatchReplacementInvitation.objects.select_related('opportunity').get(public_id=invitation_id)
    except MatchReplacementInvitation.DoesNotExist as exc:
        raise NotFound('Replacement invitation not found.') from exc
    if preliminary.candidate_id != user.pk:
        raise PermissionDenied('This replacement invitation belongs to another player.')
    # Lock the match first: every accepted invitation for the same match serializes here.
    failure = None
    accepted = False
    with transaction.atomic():
        try:
            match = Match.objects.select_for_update().select_related('sport', 'ground', 'organizer').get(pk=preliminary.opportunity.match_id)
        except Match.DoesNotExist as exc:
            raise NotFound('Match not found.') from exc
        opportunity = MatchReplacementOpportunity.objects.select_for_update().get(pk=preliminary.opportunity_id)
        invitation = MatchReplacementInvitation.objects.select_for_update().get(pk=preliminary.pk)
        now = timezone.now()
        if invitation.candidate_id != user.pk:
            raise PermissionDenied('This replacement invitation belongs to another player.')
        if invitation.status == MatchReplacementInvitation.Status.ACCEPTED:
            return match, False
        if invitation.status != MatchReplacementInvitation.Status.PENDING:
            failure = 'This invitation is no longer available.'
        elif opportunity.expires_at <= now or match.date_time <= now:
            invitation.status = MatchReplacementInvitation.Status.EXPIRED
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
            failure = 'This replacement invitation has expired.'
        elif opportunity.closed_at or match.status != 'upcoming':
            invitation.status = MatchReplacementInvitation.Status.UNAVAILABLE
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
            failure = 'The match is no longer available.'
        elif match.joined_players.count() >= match.total_players:
            invitation.status = MatchReplacementInvitation.Status.UNAVAILABLE
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
            opportunity.closed_at = now
            opportunity.save(update_fields=['closed_at', 'updated_at'])
            MatchReplacementInvitation.objects.filter(
                opportunity=opportunity, status=MatchReplacementInvitation.Status.PENDING,
            ).exclude(pk=invitation.pk).update(status=MatchReplacementInvitation.Status.UNAVAILABLE, responded_at=now, updated_at=now)
            failure = 'The match no longer has an open slot.'
        elif not _candidate_is_eligible(user, match, withdrawn_user_id=opportunity.withdrawn_user_id):
            invitation.status = MatchReplacementInvitation.Status.UNAVAILABLE
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
            failure = 'You are no longer eligible for this match.'
        else:
            match.joined_players.add(user)
            MatchParticipation.objects.create(match=match, user=user)
            invitation.status = MatchReplacementInvitation.Status.ACCEPTED
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
            opportunity.closed_at = now
            opportunity.replacement_user = user
            opportunity.save(update_fields=['closed_at', 'replacement_user', 'updated_at'])
            MatchReplacementInvitation.objects.filter(
                opportunity=opportunity, status=MatchReplacementInvitation.Status.PENDING,
            ).exclude(pk=invitation.pk).update(status=MatchReplacementInvitation.Status.UNAVAILABLE, responded_at=now, updated_at=now)
            MatchReplacementHistory.objects.create(
                opportunity=opportunity, match=match, withdrawn_user=opportunity.withdrawn_user,
                replacement_user=user,
            )
            NotificationService.create_notification(
                user=match.organizer, type='match_invite', title='Replacement player joined',
                body=f'A player accepted the open slot in your {match.sport.name} match.',
                data={'match_id': str(match.public_id)}, event_key=f'match-rescue-accepted:{opportunity.public_id}',
            )
            for participant in match.joined_players.exclude(pk=user.pk).iterator():
                NotificationService.create_notification(
                    user=participant, type='match_invite', title='Match slot filled',
                    body=f'A player accepted the open slot in your {match.sport.name} match.',
                    data={'match_id': str(match.public_id)}, event_key=f'match-rescue-accepted:{opportunity.public_id}:{participant.pk}',
                )
            if match.joined_players.count() >= match.total_players:
                _close_full_match_rescues(match, now)
            accepted = True
    if failure:
        raise MatchUnavailable(failure)
    return match, accepted


def decline_rescue_invitation(*, invitation_id, user):
    try:
        preliminary = MatchReplacementInvitation.objects.select_related('opportunity').get(public_id=invitation_id)
    except MatchReplacementInvitation.DoesNotExist as exc:
        raise NotFound('Replacement invitation not found.') from exc
    if preliminary.candidate_id != user.pk:
        raise PermissionDenied('This replacement invitation belongs to another player.')
    failure = None
    with transaction.atomic():
        match = Match.objects.select_for_update().filter(
            pk=preliminary.opportunity.match_id,
        ).first()
        if match is None:
            raise NotFound('Match not found.')
        opportunity = MatchReplacementOpportunity.objects.select_for_update().get(pk=preliminary.opportunity_id)
        invitation = MatchReplacementInvitation.objects.select_for_update().get(pk=preliminary.pk)
        now = timezone.now()
        if invitation.candidate_id != user.pk:
            raise PermissionDenied('This replacement invitation belongs to another player.')
        if invitation.status != MatchReplacementInvitation.Status.PENDING:
            failure = 'This invitation is no longer available.'
        elif opportunity.expires_at <= now:
            invitation.status = MatchReplacementInvitation.Status.EXPIRED
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
            failure = 'This replacement invitation has expired.'
        elif opportunity.closed_at or match.status != 'upcoming' or match.date_time <= now:
            invitation.status = MatchReplacementInvitation.Status.UNAVAILABLE
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
            failure = 'This invitation is no longer available.'
        else:
            invitation.status = MatchReplacementInvitation.Status.DECLINED
            invitation.responded_at = now
            invitation.save(update_fields=['status', 'responded_at', 'updated_at'])
    if failure:
        raise MatchUnavailable(failure)
    return invitation


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
    participation = MatchParticipation.objects.select_for_update().filter(
        match=match, user=user, status=MatchParticipation.Status.JOINED,
    ).first()
    if participation is None:
        participation = MatchParticipation.objects.create(match=match, user=user)
    if participation:
        participation.status = MatchParticipation.Status.LEFT
        participation.ended_at = timezone.now()
        participation.save(update_fields=['status', 'ended_at', 'updated_at'])
    NotificationService.create_notification(
        user=match.organizer, actor=user, type='match_invite', title='Player left your match',
        body=f'{user.username} left your {match.sport.name} match. The slot is available again.',
        data={'match_id': str(match.public_id)},
        event_key=f'match-left:{match.pk}:{user.pk}:{participation.pk if participation else timezone.now().timestamp()}',
    )
    _notify_rescue_candidates(match, user)
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
    participation = MatchParticipation.objects.select_for_update().filter(
        match=match, user=player, status=MatchParticipation.Status.JOINED,
    ).first()
    if participation is None:
        participation = MatchParticipation.objects.create(match=match, user=player)
    if participation:
        participation.status = MatchParticipation.Status.REMOVED
        participation.ended_at = timezone.now()
        participation.reason = reason.strip()
        participation.save(update_fields=['status', 'ended_at', 'reason', 'updated_at'])
    suffix = f' Reason: {reason.strip()}' if reason.strip() else ''
    NotificationService.create_notification(
        user=player, actor=organizer, type='match_invite', title='Removed from match',
        body=f'You were removed from {match.sport.name} at {match.ground.name}.{suffix}',
        data={'match_id': str(match.public_id), 'reason': reason.strip()},
        # A participant may rejoin after leaving/removal. Key each removal to
        # its persisted participation so a later valid removal still notifies.
        event_key=f'match-removed:{match.pk}:{player.pk}:{participation.pk}',
    )
    return match


@transaction.atomic
def cancel_match(*, match_id, organizer):
    try:
        match = Match.objects.select_for_update().select_related('sport', 'ground').get(pk=match_id)
    except Match.DoesNotExist as exc:
        raise NotFound('Match not found.') from exc
    if match.organizer_id != organizer.id:
        raise PermissionDenied('Only the match organizer can cancel it.')
    if match.status != 'upcoming' or match.date_time <= timezone.now():
        raise MatchUnavailable('Only an upcoming match can be cancelled.')
    match.status = 'cancelled'
    match.save(update_fields=['status', 'updated_at'])
    now = timezone.now()
    match.replacement_opportunities.filter(closed_at__isnull=True).update(closed_at=now, updated_at=now)
    MatchReplacementInvitation.objects.filter(
        opportunity__match=match, status=MatchReplacementInvitation.Status.PENDING,
    ).update(status=MatchReplacementInvitation.Status.UNAVAILABLE, responded_at=now, updated_at=now)
    # A cancelled match cannot accept further schedule votes. Preserve the
    # poll and vote history, but close any poll that has not been finalized.
    from my_app.models import MatchTimePoll
    match.time_polls.filter(
        status__in=[MatchTimePoll.Status.OPEN, MatchTimePoll.Status.TIE],
    ).update(
        status=MatchTimePoll.Status.CLOSED,
        version=F('version') + 1,
        updated_at=now,
    )
    for participant in match.joined_players.exclude(pk=organizer.pk).iterator():
        NotificationService.create_notification(
            user=participant, actor=organizer, type='match_invite', title='Match cancelled',
            body=f'The organizer cancelled the {match.sport.name} match.',
            data={'match_id': str(match.public_id)}, event_key=f'match-cancelled:{match.pk}:{participant.pk}',
        )
    return match


@transaction.atomic
def update_match(*, match_id, organizer, changes):
    try:
        match = Match.objects.select_for_update().select_related('sport').get(pk=match_id)
    except Match.DoesNotExist as exc:
        raise NotFound('Match not found.') from exc
    if match.organizer_id != organizer.id:
        raise PermissionDenied('Only the match organizer can edit it.')
    if not _participants_can_change(match):
        raise MatchUnavailable('Only an upcoming match can be edited.')
    capacity = changes.get('total_players', match.total_players)
    if capacity < match.joined_players.count():
        raise MatchUnavailable('Capacity cannot be lower than the current joined player count.')
    material_fields = {'title', 'description', 'ground', 'date_time', 'end_date_time', 'total_players'}
    for field, value in changes.items():
        setattr(match, field, value)
    match.save(update_fields=[*changes.keys(), 'updated_at'])
    if material_fields & changes.keys():
        for participant in match.joined_players.exclude(pk=organizer.pk).iterator():
            NotificationService.create_notification(
                user=participant, actor=organizer, type='match_invite', title='Match details updated',
                body=f'The organizer updated the {match.sport.name} match details.',
                data={'match_id': str(match.public_id)},
                event_key=f'match-updated:{match.pk}:{match.updated_at.isoformat()}:{participant.pk}',
            )
    return match

"""Transactional match-time polling; votes are advisory until organizer finalization."""
from collections import Counter
from datetime import timezone as datetime_timezone

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from my_app.models import Match, MatchTimePoll, MatchTimePollOption, MatchTimePollVoter, MatchTimePollVote
from my_app.services.notifications import NotificationService


class MatchTimePollService:
    @staticmethod
    def _is_eligible(match, user):
        return match.organizer_id == user.id or match.joined_players.filter(pk=user.pk).exists()

    @classmethod
    @transaction.atomic
    def create(cls, *, match_id, actor, deadline, options):
        try:
            match = Match.objects.select_for_update().get(pk=match_id)
        except Match.DoesNotExist as exc:
            raise NotFound('Match not found.') from exc
        if match.organizer_id != actor.id:
            raise PermissionDenied('Only the match organizer can create a schedule poll.')
        if match.status != 'upcoming' or match.date_time <= timezone.now():
            raise ValidationError('A poll can be created only for an upcoming match.')
        now = timezone.now()
        if deadline <= now or deadline >= match.date_time:
            raise ValidationError('Poll deadline must be in the future and before the current match time.')
        if len(options) < 2 or len(options) > 8:
            raise ValidationError({'options': 'Provide between 2 and 8 proposed time options.'})
        seen = set()
        duration = match.end_date_time - match.date_time
        for option in options:
            start, end = option['starts_at'], option.get('ends_at')
            if timezone.is_naive(start) or (end is not None and timezone.is_naive(end)):
                raise ValidationError({'options': 'Each date/time must include a timezone offset.'})
            if start <= now:
                raise ValidationError({'options': 'Proposed match times must be in the future.'})
            if end is None:
                option['ends_at'] = start + duration
            elif end <= start:
                raise ValidationError({'options': 'Every option end must be after its start.'})
            key = start.astimezone(datetime_timezone.utc)
            if key in seen:
                raise ValidationError({'options': 'Duplicate proposed start times are not allowed.'})
            seen.add(key)
        MatchTimePoll.objects.select_for_update().filter(match=match, status=MatchTimePoll.Status.OPEN, deadline__lte=now).update(status=MatchTimePoll.Status.CLOSED, updated_at=now)
        active = MatchTimePoll.objects.select_for_update().filter(match=match, status__in=[MatchTimePoll.Status.OPEN, MatchTimePoll.Status.TIE], deadline__gt=now).first()
        if active:
            raise ValidationError('This match already has an open schedule poll.')
        poll = MatchTimePoll.objects.create(match=match, creator=actor, deadline=deadline)
        MatchTimePollOption.objects.bulk_create([
            MatchTimePollOption(poll=poll, starts_at=item['starts_at'], ends_at=item['ends_at']) for item in options
        ])
        voter_ids = set(match.joined_players.values_list('id', flat=True)) | {match.organizer_id}
        MatchTimePollVoter.objects.bulk_create([MatchTimePollVoter(poll=poll, user_id=user_id) for user_id in voter_ids])
        users = match.joined_players.exclude(pk=actor.pk)
        for user in users:
            NotificationService.create_notification(
                user=user, actor=actor, type='match_invite', title='Schedule poll opened',
                body=f'Vote on a new time for your {match.sport.name} match before {deadline:%b %d, %Y %I:%M %p}.',
                data={'match_id': match.public_id.hex, 'poll_id': poll.public_id.hex},
                event_key=f'match-time-poll:{poll.pk}:opened:{user.pk}',
            )
        return poll

    @classmethod
    @transaction.atomic
    def vote(cls, *, poll_id, actor, option_id):
        try:
            poll = MatchTimePoll.objects.select_for_update().select_related('match').get(public_id=poll_id)
        except MatchTimePoll.DoesNotExist as exc:
            raise NotFound('Schedule poll not found.') from exc
        now = timezone.now()
        if poll.status not in (MatchTimePoll.Status.OPEN, MatchTimePoll.Status.TIE) or poll.deadline <= now:
            raise ValidationError('This schedule poll is closed for voting.')
        if not poll.eligible_voters.filter(user=actor).exists() or not cls._is_eligible(poll.match, actor):
            raise PermissionDenied('Only participants at poll creation can vote.')
        option = poll.options.filter(public_id=option_id).first()
        if not option:
            raise NotFound('Poll option not found.')
        vote = MatchTimePollVote.objects.select_for_update().filter(poll=poll, voter=actor, is_active=True).first()
        if vote and vote.option_id == option.id:
            return vote, False
        if vote:
            vote.is_active = False
            vote.changed_at = now
            vote.save(update_fields=['is_active', 'changed_at', 'updated_at'])
        vote = MatchTimePollVote.objects.create(poll=poll, voter=actor, option=option)
        return vote, True

    @classmethod
    def finalize(cls, *, poll_id, actor, option_id=None, expected_version=None):
        try:
            with transaction.atomic():
                try:
                    poll = MatchTimePoll.objects.select_for_update().select_related('match', 'creator').get(public_id=poll_id)
                except MatchTimePoll.DoesNotExist as exc:
                    raise NotFound('Schedule poll not found.') from exc
                match = Match.objects.select_for_update().get(pk=poll.match_id)
                if match.organizer_id != actor.id:
                    raise PermissionDenied('Only the match organizer can finalize its schedule poll.')
                if poll.status == MatchTimePoll.Status.FINALIZED:
                    raise ValidationError('This poll has already been finalized.')
                if poll.status == MatchTimePoll.Status.CLOSED:
                    raise ValidationError('This poll has been closed.')
                if expected_version is not None and int(expected_version) != poll.version:
                    raise ValidationError('Poll changed since it was loaded. Refresh before finalizing.')
                if match.status != 'upcoming' or match.date_time <= timezone.now():
                    raise ValidationError('Only upcoming matches can be rescheduled.')
                options = list(poll.options.all())
                counts = Counter(poll.votes.filter(is_active=True).values_list('option_id', flat=True))
                if option_id is None:
                    if not counts:
                        raise ValidationError('At least one vote is required before finalization.')
                    max_votes = max(counts.values())
                    winners = [item for item in options if counts[item.id] == max_votes]
                    if len(winners) != 1:
                        poll.status = MatchTimePoll.Status.TIE
                        poll.version += 1
                        poll.save(update_fields=['status', 'version', 'updated_at'])
                        return poll, None
                    selected = winners[0]
                else:
                    selected = next((item for item in options if item.public_id == option_id), None)
                    if not selected:
                        raise NotFound('Poll option not found.')
                conflict = Match.objects.select_for_update().filter(
                    ground_id=match.ground_id, status='upcoming', date_time__lt=selected.ends_at,
                    end_date_time__gt=selected.starts_at,
                ).exclude(pk=match.pk).exists()
                if conflict:
                    raise ValidationError('Another match is already scheduled at this ground during the selected time.')
                match.date_time, match.end_date_time = selected.starts_at, selected.ends_at
                match.save(update_fields=['date_time', 'end_date_time', 'updated_at'])
                poll.status, poll.finalized_option, poll.finalized_by, poll.finalized_at = MatchTimePoll.Status.FINALIZED, selected, actor, timezone.now()
                poll.version += 1
                poll.save(update_fields=['status', 'finalized_option', 'finalized_by', 'finalized_at', 'version', 'updated_at'])
                for user in match.joined_players.all():
                    NotificationService.create_notification(
                        user=user, actor=actor, type='match_invite', title='Match time finalized',
                        body=f'{match.sport.name} at {match.ground.name} is now scheduled for {selected.starts_at:%b %d, %Y %I:%M %p}.',
                        data={'match_id': match.public_id.hex, 'poll_id': poll.public_id.hex},
                        event_key=f'match-time-poll:{poll.pk}:finalized:{user.pk}',
                    )
                return poll, selected
        except ValueError as exc:
            raise ValidationError('expected_version must be an integer.') from exc

    @classmethod
    @transaction.atomic
    def close(cls, *, poll_id, actor):
        poll = MatchTimePoll.objects.select_for_update().select_related('match').filter(public_id=poll_id).first()
        if not poll:
            raise NotFound('Schedule poll not found.')
        if poll.match.organizer_id != actor.id:
            raise PermissionDenied('Only the match organizer can close its schedule poll.')
        if poll.status == MatchTimePoll.Status.FINALIZED:
            raise ValidationError('A finalized poll cannot be closed.')
        if poll.status == MatchTimePoll.Status.CLOSED:
            return poll
        poll.status = MatchTimePoll.Status.CLOSED
        poll.version += 1
        poll.save(update_fields=['status', 'version', 'updated_at'])
        return poll

    @classmethod
    def serialize(cls, poll, *, user=None):
        votes = poll.votes.filter(is_active=True)
        counts = Counter(votes.values_list('option_id', flat=True))
        options = list(poll.options.all())
        current = votes.filter(voter=user).select_related('option').first() if user and user.is_authenticated else None
        status = poll.status
        if status == MatchTimePoll.Status.OPEN and poll.deadline <= timezone.now():
            status = MatchTimePoll.Status.CLOSED
        return {
            'public_id': str(poll.public_id), 'match_id': str(poll.match.public_id),
            'creator_id': str(poll.creator.public_id), 'deadline': poll.deadline,
            'status': status, 'version': poll.version,
            'finalized_option_id': str(poll.finalized_option.public_id) if poll.finalized_option_id else None,
            'finalized_at': poll.finalized_at,
            'my_vote_option_id': str(current.option.public_id) if current else None,
            'options': [{'public_id': str(option.public_id), 'starts_at': option.starts_at,
                         'ends_at': option.ends_at, 'vote_count': counts[option.id]}
                        for option in options],
        }

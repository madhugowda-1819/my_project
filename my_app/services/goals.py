from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from my_app.models import Match, NotificationPreference, PracticeCompletion, SportsGoal
from my_app.services.notifications import NotificationService


def _tz(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError):
        raise ValidationError({'reminder_timezone': ['Use a valid IANA timezone.']})


def _source_count(goal):
    start = goal.start_date
    tz = _tz(goal.reminder_timezone)
    today = timezone.now().astimezone(tz).date()
    end = min(goal.end_date, today)
    if today < start:
        return 0
    if goal.progress_source == SportsGoal.Source.MANUAL:
        return min(goal.manual_progress, goal.target)
    if goal.progress_source == SportsGoal.Source.CHECKLIST_COMPLETIONS:
        count = PracticeCompletion.objects.filter(user=goal.user, practice_date__range=(start, end)).count()
        return min(count, goal.target)
    if goal.progress_source == SportsGoal.Source.PRACTICE_DAYS:
        return min(PracticeCompletion.objects.filter(
            user_id=goal.user_id, practice_date__gte=start, practice_date__lte=end,
        ).values('practice_date').distinct().count(), goal.target)
    if goal.progress_source == SportsGoal.Source.MATCHES_COMPLETED:
        matches = Match.objects.filter(status='completed').filter(
            Q(organizer=goal.user) | Q(joined_players__pk=goal.user.pk),
        ).distinct()
        match_dates = [timezone.localtime(match.end_date_time, tz).date() for match in matches]
        count = sum(1 for day in match_dates if start <= day <= goal.end_date and day <= today)
        return min(count, goal.target)
    return 0


def serialize_goal(goal):
    progress = _source_count(goal)
    if goal.status == SportsGoal.Status.COMPLETED:
        progress = goal.target
    return {
        'public_id': str(goal.public_id), 'title': goal.title,
        'target': goal.target, 'unit': goal.unit,
        'start_date': goal.start_date.isoformat(), 'end_date': goal.end_date.isoformat(),
        'progress_source': goal.progress_source,
        'progress_source_label': goal.get_progress_source_display(),
        'progress_mode': 'manual' if goal.progress_source == SportsGoal.Source.MANUAL else 'calculated',
        'progress': progress, 'remaining': max(0, goal.target - progress),
        'percentage': round(progress / goal.target * 100),
        'status': goal.status, 'reminder_enabled': goal.reminder_enabled,
        'reminder_time': goal.reminder_time.strftime('%H:%M') if goal.reminder_time else None,
        'reminder_timezone': goal.reminder_timezone,
        'created_at': goal.created_at.isoformat(), 'updated_at': goal.updated_at.isoformat(),
    }


class SportsGoalService:
    @staticmethod
    def list_for(*, user):
        # Calculated goals are marked complete once verified progress reaches target.
        for goal in SportsGoal.objects.filter(user=user, status=SportsGoal.Status.ACTIVE):
            if _source_count(goal) >= goal.target:
                goal.status = SportsGoal.Status.COMPLETED
                goal.save(update_fields=['status', 'updated_at'])
        return [serialize_goal(goal) for goal in SportsGoal.objects.filter(user=user).order_by('-created_at')]

    @staticmethod
    @transaction.atomic
    def create(*, user, data):
        if data['end_date'] < data['start_date']:
            raise ValidationError({'end_date': ['End date must be on or after start date.']})
        if (data['end_date'] - data['start_date']).days > 366:
            raise ValidationError({'end_date': ['Goal duration cannot exceed 367 days.']})
        source = data['progress_source']
        allowed_units = {
            SportsGoal.Source.PRACTICE_DAYS: {'sessions', 'days'},
            SportsGoal.Source.MATCHES_COMPLETED: {'matches'},
            SportsGoal.Source.CHECKLIST_COMPLETIONS: {'activities', 'completions'},
            SportsGoal.Source.MANUAL: {'sessions', 'minutes', 'repetitions', 'points'},
        }
        if data['unit'].lower() not in allowed_units[source]:
            raise ValidationError({'unit': [f'Invalid unit for {source}.']})
        _tz(data.get('reminder_timezone', 'Asia/Kolkata'))
        goal = SportsGoal.objects.create(user=user, **data)
        return serialize_goal(goal)

    @staticmethod
    @transaction.atomic
    def update(*, user, public_id, changes):
        try:
            goal = SportsGoal.objects.select_for_update().get(user=user, public_id=public_id)
        except SportsGoal.DoesNotExist as exc:
            raise NotFound('Goal not found.') from exc
        if goal.status in (SportsGoal.Status.ARCHIVED, SportsGoal.Status.COMPLETED):
            raise ValidationError({'status': ['Completed or archived goals are read-only.']})
        for key, value in changes.items():
            setattr(goal, key, value)
        if goal.end_date < goal.start_date:
            raise ValidationError({'end_date': ['End date must be on or after start date.']})
        if goal.progress_source == SportsGoal.Source.MANUAL and goal.manual_progress > goal.target:
            raise ValidationError({'manual_progress': ['Progress cannot exceed the target.']})
        if goal.reminder_enabled:
            _tz(goal.reminder_timezone)
            if not goal.reminder_time:
                raise ValidationError({'reminder_time': ['A reminder time is required when reminders are enabled.']})
        goal.save()
        progress = _source_count(goal)
        if progress >= goal.target:
            goal.status = SportsGoal.Status.COMPLETED
            goal.save(update_fields=['status', 'updated_at'])
        return serialize_goal(goal)

    @staticmethod
    @transaction.atomic
    def transition(*, user, public_id, status):
        try:
            goal = SportsGoal.objects.select_for_update().get(user=user, public_id=public_id)
        except SportsGoal.DoesNotExist as exc:
            raise NotFound('Goal not found.') from exc
        if goal.status == SportsGoal.Status.ARCHIVED:
            raise ValidationError({'status': ['Archived goals cannot be changed.']})
        if status == SportsGoal.Status.COMPLETED and _source_count(goal) < goal.target:
            raise ValidationError({'status': ['A goal can only be completed when its verified or manual progress reaches the target.']})
        goal.status = status
        goal.save(update_fields=['status', 'updated_at'])
        return serialize_goal(goal)

    @staticmethod
    def summaries(*, user, today=None):
        today = today or timezone.localdate()
        def range_summary(start, end):
            goals = SportsGoal.objects.filter(user=user, start_date__lte=end, end_date__gte=start).exclude(status=SportsGoal.Status.ARCHIVED)
            return {'goal_count': goals.count(), 'completed_count': goals.filter(status=SportsGoal.Status.COMPLETED).count(), 'progress': [serialize_goal(goal) for goal in goals.order_by('end_date')]}
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        return {'week': range_summary(week_start, today), 'month': range_summary(month_start, today)}

    @staticmethod
    def deliver_due_reminders(*, now=None, limit=500):
        now = now or timezone.now()
        goals = list(SportsGoal.objects.select_for_update(skip_locked=True).filter(
            status=SportsGoal.Status.ACTIVE, reminder_enabled=True,
        ).order_by('reminder_time')[:limit])
        sent = 0
        for goal in goals:
            tz = _tz(goal.reminder_timezone)
            local_now = now.astimezone(tz)
            if not goal.start_date <= local_now.date() <= goal.end_date:
                continue
            if goal.reminder_last_sent_date == local_now.date() or not goal.reminder_time:
                continue
            reminder_at = datetime.combine(local_now.date(), goal.reminder_time, tzinfo=tz)
            if not (reminder_at <= local_now < reminder_at + timedelta(minutes=10)):
                continue
            pref = NotificationPreference.objects.filter(user=goal.user).first()
            if pref and not pref.event_notifications:
                goal.reminder_last_sent_date = local_now.date()
                goal.save(update_fields=['reminder_last_sent_date', 'updated_at'])
                continue
            NotificationService.create_notification(
                user=goal.user, type='reminder', title='Practice goal reminder',
                body=f'Review your goal: {goal.title}.',
                data={'goal_id': str(goal.public_id)}, priority=2,
                event_key=f'sports-goal-reminder:{goal.public_id}:{local_now.date().isoformat()}',
            )
            goal.reminder_last_sent_date = local_now.date()
            goal.save(update_fields=['reminder_last_sent_date', 'updated_at'])
            sent += 1
        return sent

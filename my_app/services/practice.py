from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from my_app.models import PracticeActivity, PracticeCompletion


DEFAULT_ACTIVITIES = (
    ('Warm-up', 'Prepare your body before practice.'),
    ('Skill practice', 'Work on a sport-specific skill.'),
    ('Cooldown', 'Wind down after your session.'),
    ('Hydration', 'Remember to hydrate during practice.'),
    ('Stretching', 'Stretch comfortably after activity.'),
)


def _timezone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValidationError({'timezone': ['Use a valid IANA timezone, such as Asia/Kolkata.']})


def current_date(timezone_name='UTC'):
    """Return today's date in the same explicit timezone used by practice APIs."""
    tz = _timezone(timezone_name)
    return timezone.now().astimezone(tz).date()


def ensure_default_activities(user):
    for order, (name, description) in enumerate(DEFAULT_ACTIVITIES):
        PracticeActivity.objects.get_or_create(
            user=user, name=name,
            defaults={'description': description, 'sort_order': order, 'is_default': True},
        )


def day_payload(*, user, practice_date, tz):
    ensure_default_activities(user)
    activities = list(user.practice_activities.filter(is_active=True))
    completions = {
        row.activity_id: row for row in PracticeCompletion.objects.filter(
            user=user, practice_date=practice_date, activity_id__in=[a.id for a in activities],
        )
    }
    total = len(activities)
    completed = len(completions)
    return {
        'date': practice_date.isoformat(),
        'timezone': tz.key,
        'activities': [{
            'id': str(activity.public_id), 'name': activity.name,
            'description': activity.description, 'sort_order': activity.sort_order,
            'is_default': activity.is_default,
            'completed': activity.id in completions,
            'completed_at': timezone.localtime(completions[activity.id].completed_at, tz).isoformat() if activity.id in completions else None,
        } for activity in activities],
        'completed_count': completed,
        'total_count': total,
        'completion_percentage': round((completed / total) * 100) if total else 0,
        'streak': streak_for(user=user, today=timezone.now().astimezone(tz).date(), tz=tz),
    }


def streak_for(*, user, today, tz):
    # A day counts when every activity that was active on that day was completed.
    # Yesterday's completion keeps a streak visible on a not-yet-completed today.
    activities = list(user.practice_activities.all())
    if not activities:
        return 0
    completion_map = {}
    for row in PracticeCompletion.objects.filter(user=user).values('practice_date', 'activity_id'):
        completion_map.setdefault(row['practice_date'], set()).add(row['activity_id'])
    day = today
    if day not in completion_map:
        day -= timedelta(days=1)
    if not activities:
        return 0
    required_ids = {activity.id for activity in activities if activity.created_at.astimezone(tz).date() <= day}
    if not required_ids or not required_ids.issubset(completion_map.get(day, set())):
        day -= timedelta(days=1)
    streak = 0
    while day in completion_map:
        required_ids = {activity.id for activity in activities if activity.created_at.astimezone(tz).date() <= day}
        if not required_ids or not required_ids.issubset(completion_map[day]):
            break
        streak += 1
        day -= timedelta(days=1)
    return streak


class PracticeService:
    @staticmethod
    def current_date(timezone_name='UTC'):
        return current_date(timezone_name)

    @staticmethod
    def get_day(*, user, practice_date, timezone_name):
        tz = _timezone(timezone_name)
        if practice_date > timezone.now().astimezone(tz).date():
            raise ValidationError({'date': ['Future dates are not available.']})
        return day_payload(user=user, practice_date=practice_date, tz=tz)

    @staticmethod
    @transaction.atomic
    def set_completion(*, user, activity_id, practice_date, timezone_name, completed):
        tz = _timezone(timezone_name)
        if practice_date > timezone.now().astimezone(tz).date():
            raise ValidationError({'date': ['Future dates cannot be completed.']})
        ensure_default_activities(user)
        try:
            activity = PracticeActivity.objects.get(public_id=activity_id, user=user)
        except PracticeActivity.DoesNotExist as exc:
            raise NotFound('Practice activity not found.') from exc
        if not activity.is_active:
            raise ValidationError({'activity': ['Inactive activities cannot be completed.']})
        if completed:
            PracticeCompletion.objects.get_or_create(
                user=user, activity=activity, practice_date=practice_date,
                defaults={'activity_name': activity.name},
            )
        else:
            PracticeCompletion.objects.filter(user=user, activity=activity, practice_date=practice_date).delete()
        return day_payload(user=user, practice_date=practice_date, tz=tz)

    @staticmethod
    @transaction.atomic
    def create_activity(*, user, name, description='', sort_order=None):
        if sort_order is None:
            sort_order = (user.practice_activities.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0) + 1
        try:
            with transaction.atomic():
                return PracticeActivity.objects.create(
                    user=user, name=name.strip(), description=description.strip(),
                    sort_order=sort_order,
                )
        except IntegrityError as exc:
            raise ValidationError({'name': ['An activity with this name already exists.']}) from exc

    @staticmethod
    @transaction.atomic
    def update_activity(*, user, public_id, changes):
        try:
            activity = PracticeActivity.objects.get(user=user, public_id=public_id)
        except PracticeActivity.DoesNotExist as exc:
            raise NotFound('Practice activity not found.') from exc
        for field in ('name', 'description', 'sort_order', 'is_active'):
            if field in changes:
                setattr(activity, field, changes[field])
        try:
            with transaction.atomic():
                activity.save()
        except IntegrityError as exc:
            raise ValidationError({'name': ['An activity with this name already exists.']}) from exc
        return activity

    @staticmethod
    def history(*, user, start_date, end_date, timezone_name):
        tz = _timezone(timezone_name)
        if end_date > timezone.now().astimezone(tz).date() or start_date > end_date:
            raise ValidationError({'date': ['Use a past or current date range with start before end.']})
        if (end_date - start_date).days > 365:
            raise ValidationError({'date': ['History range cannot exceed 366 days.']})
        rows = PracticeCompletion.objects.filter(user=user, practice_date__gte=start_date, practice_date__lte=end_date)
        grouped = {}
        for row in rows:
            grouped.setdefault(row.practice_date, []).append(row)
        results = []
        day = start_date
        while day <= end_date:
            done = grouped.get(day, [])
            applicable = {
                activity.id for activity in user.practice_activities.all()
                if activity.created_at.astimezone(tz).date() <= day
            }
            relevant = [row for row in done if row.activity_id in applicable]
            total = max(len(applicable), len(relevant))
            completed = len(relevant)
            results.append({'date': day.isoformat(), 'completed_count': completed, 'total_count': total, 'completion_percentage': round(completed / total * 100) if total else 0, 'activities': [{'name': row.activity_name, 'completed_at': timezone.localtime(row.completed_at, tz).isoformat()} for row in done]})
            day += timedelta(days=1)
        return {'timezone': tz.key, 'days': results, 'streak': streak_for(user=user, today=timezone.now().astimezone(tz).date(), tz=tz)}

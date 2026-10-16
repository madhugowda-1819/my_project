"""Explainable, rule-based sport training suggestions (not medical advice or AI)."""

from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from rest_framework.exceptions import ValidationError

from my_app.models import PracticeCompletion, Sport, SportsGoal, UserSport


GOALS = {
    'general_fitness': ('General fitness', 'Build a balanced practice habit'),
    'skill_development': ('Skill development', 'Improve control and repeatable technique'),
    'endurance': ('Endurance', 'Build sustained, sport-specific activity'),
    'strength': ('Strength', 'Practice controlled bodyweight strength and movement'),
    'flexibility': ('Mobility and flexibility', 'Include comfortable mobility and range-of-motion work'),
}

SPORT_DRILLS = {
    'cricket': ['Batting footwork and shadow strokes', 'Target bowling accuracy', 'Catching and ground-fielding repetitions'],
    'football': ['Close-control dribbling patterns', 'Short passing against a wall or partner', 'Controlled change-of-direction runs'],
    'soccer': ['Close-control dribbling patterns', 'Short passing against a wall or partner', 'Controlled change-of-direction runs'],
    'badminton': ['Shadow footwork to all court corners', 'Racket control and placement rally', 'Split-step and recovery movement'],
    'tennis': ['Shadow swings and split-step footwork', 'Controlled groundstroke rally', 'Serve toss and placement practice'],
    'basketball': ['Stationary ball-handling sequence', 'Form shooting at a comfortable pace', 'Defensive slides with controlled recovery'],
    'volleyball': ['Passing platform control against a wall or partner', 'Approach footwork without maximal jumping', 'Serve toss and target practice'],
    'table tennis': ['Forehand and backhand shadow strokes', 'Controlled placement rally', 'Side-step positioning and recovery'],
    'hockey': ['Stick control through a simple cone path', 'Push-pass accuracy to a target', 'Controlled receiving and movement'],
}


def build_training_plan(*, user, sport_id, goal, duration_minutes, days_per_week=3, skill_level=None, timezone_name='Asia/Kolkata'):
    """Build a session and a weekly cadence from user-owned sport preferences only."""
    try:
        sport = Sport.objects.get(pk=sport_id, is_active=True)
    except Sport.DoesNotExist as exc:
        raise ValidationError({'sport_id': 'Choose an active SportMate sport.'}) from exc
    preference = UserSport.objects.filter(user=user, sport=sport).first()
    if preference is None:
        raise ValidationError({'sport_id': 'Add this sport to your profile before requesting its plan.'})
    if goal not in GOALS:
        raise ValidationError({'goal': 'Choose a supported training goal.'})
    if not 15 <= duration_minutes <= 120:
        raise ValidationError({'duration_minutes': 'Choose 15 to 120 minutes.'})
    if not 1 <= days_per_week <= 7:
        raise ValidationError({'days_per_week': 'Choose 1 to 7 days per week.'})
    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValidationError({'timezone': 'Use a valid IANA timezone.'})
    skill = skill_level or preference.skill_level
    if skill not in UserSport.SkillLevel.values:
        raise ValidationError({'skill_level': 'Choose a valid skill level.'})

    warmup = max(3, round(duration_minutes * 0.15))
    cooldown = max(3, round(duration_minutes * 0.10))
    drill_minutes = duration_minutes - warmup - cooldown
    drill_names = SPORT_DRILLS.get(sport.name.casefold(), [
        f'{sport.name} fundamentals: controlled technique repetitions',
        f'{sport.name} movement and positioning practice',
        f'{sport.name} accuracy or timing drill at a comfortable pace',
    ])
    base, remainder = divmod(drill_minutes, len(drill_names))
    drills = []
    for index, name in enumerate(drill_names):
        allotted = base + (1 if index < remainder else 0)
        if allotted:
            drills.append({
                'title': name,
                'duration_minutes': allotted,
                'sets': 2 if skill in ('beginner', 'intermediate') else 3,
                'cue': 'Use a controlled pace, focus on form, and pause if you feel pain or unwell.',
                'reason': f"Selected for {GOALS[goal][0].lower()} at {skill} level.",
            })

    weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    step = max(1, 7 // days_per_week)
    session_days = [weekdays[(index * step) % 7] for index in range(days_per_week)]
    today = timezone.now().astimezone(tz).date()
    recent_completions = PracticeCompletion.objects.filter(
        user=user, practice_date__gte=today - timedelta(days=6), practice_date__lte=today,
    ).count()
    active_goals = list(SportsGoal.objects.filter(user=user, status=SportsGoal.Status.ACTIVE)
                        .order_by('end_date').values_list('title', flat=True)[:3])
    if recent_completions >= 5:
        recovery = 'You have logged several checklist activities in the last 7 days. Keep recovery days in your schedule and reduce effort if tired.'
    else:
        recovery = 'Leave time for rest and hydration. Stop if you feel pain, dizziness, or unwell.'
    weekly_plan = [
        {'day': day, 'focus': GOALS[goal][0], 'duration_minutes': duration_minutes,
         'suggestion': f'{sport.name} practice session; keep the effort comfortable and take recovery days as needed.'}
        for day in session_days
    ]
    return {
        'method': 'rule_based',
        'method_label': 'Rule-based suggestions',
        'disclaimer': 'General training ideas only—not medical advice or a promise of results. Adjust to your comfort and seek qualified medical advice for health concerns.',
        'generated_for': {'sport': {'id': sport.pk, 'name': sport.name}, 'skill_level': skill,
                          'goal': goal, 'goal_label': GOALS[goal][0], 'duration_minutes': duration_minutes,
                          'days_per_week': days_per_week, 'week_of': today.isoformat()},
        'based_on': [
            'The sport and skill level you selected or saved in your profile',
            'Your selected training goal and session duration',
            f'{recent_completions} Daily Practice checklist activities completed in the last 7 days',
            f'{len(active_goals)} active SportMate goal(s)' if active_goals else 'No active SportMate goals found',
        ],
        'recent_context': {'checklist_completions_last_7_days': recent_completions, 'active_goal_titles': active_goals},
        'warm_up': {'title': 'Warm up', 'duration_minutes': warmup,
                    'ideas': ['Easy movement to gradually raise your pace', 'Dynamic, comfortable mobility for the movements used in your sport']},
        'drills': drills,
        'cool_down': {'title': 'Cool down', 'duration_minutes': cooldown,
                      'ideas': ['Slow down with easy movement', 'Gentle stretches within a comfortable range; do not force a stretch']},
        'recovery_reminder': recovery,
        'weekly_plan': weekly_plan,
    }

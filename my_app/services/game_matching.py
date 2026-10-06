"""Deterministic, rule-based recommendations for upcoming SportMate games.

This is deliberately not machine learning. Candidate filtering is performed in
the database; only a bounded compatible set is scored in Python.
"""
from dataclasses import dataclass
from datetime import time

from django.db.models import Count, F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from my_app.models import AvailabilitySlot, Game, GamePlayer, UserSport
from my_app.services.locations import nearby_queryset, validate_coordinates


WEIGHTS = {
    'sport': 30,
    'skill': 20,
    'distance': 20,
    'time': 15,
    'capacity': 10,
    'reliability': 5,
}
MAX_CANDIDATES = 500
SKILL_ORDER = {choice: index for index, (choice, _) in enumerate(UserSport.SkillLevel.choices)}
SKILL_SCORES = {0: 20, 1: 15, 2: 8, 3: 3}
DISTANCE_SCORES = ((2, 20), (5, 17), (10, 12), (20, 6))


@dataclass(frozen=True)
class GameMatchScore:
    score: int
    distance_km: float | None
    reasons: list[str]


class GameMatchingService:
    @staticmethod
    def calculate_sport_score(user_sport):
        if not user_sport:
            return 0
        return WEIGHTS['sport'] if user_sport.preferred else 20

    @staticmethod
    def calculate_skill_score(user_skill, game_skill):
        if not game_skill:
            return WEIGHTS['skill']
        if user_skill not in SKILL_ORDER or game_skill not in SKILL_ORDER:
            return 0
        return SKILL_SCORES.get(abs(SKILL_ORDER[user_skill] - SKILL_ORDER[game_skill]), 0)

    @staticmethod
    def calculate_distance_score(distance_km):
        if distance_km is None:
            return 0
        for threshold, score in DISTANCE_SCORES:
            if distance_km <= threshold:
                return score
        return 0

    @staticmethod
    def _time_band(start_time):
        if start_time < time(12):
            return 'morning'
        if start_time < time(17):
            return 'afternoon'
        return 'evening'

    @classmethod
    def calculate_time_score(cls, *, game, availability_slots, preferred_times, profile_availability):
        """Score explicit weekly availability before softer time preferences."""
        weekday_slots = [slot for slot in availability_slots if slot.day_of_week == game.game_date.weekday()]
        if availability_slots:
            for slot in weekday_slots:
                if slot.start_time <= game.start_time and slot.end_time >= game.end_time:
                    return 15, 'Fits your available time'
                if slot.start_time < game.end_time and slot.end_time > game.start_time:
                    return 12, 'Partly fits your available time'
            # A user with defined weekly slots has expressed an unavailable time.
            return 0, None

        preferences = {str(value).strip().lower() for value in (preferred_times or []) if str(value).strip()}
        blocked = set()
        if isinstance(profile_availability, dict):
            blocked = {str(value).strip().lower() for value in profile_availability.get('blocked_times', [])}
        band = cls._time_band(game.start_time)
        if band in blocked or game.game_date.isoformat() in blocked:
            return 0, None
        if not preferences:
            return 4, None
        if band in preferences:
            return 12, f'Fits your preferred {band} schedule'
        # Weekend is a softer preference, because it provides no hour range.
        if game.game_date.weekday() >= 5 and 'weekend' in preferences:
            return 7, 'Fits your weekend preference'
        return 4, None

    @staticmethod
    def calculate_capacity_score(available_slots):
        if available_slots <= 0:
            return 0
        if available_slots == 1:
            return 10
        if available_slots <= 3:
            return 8
        if available_slots <= 5:
            return 6
        return 4

    @staticmethod
    def calculate_reliability_score(completed, cancelled):
        """Uses only backend-controlled historical game states; neutral if new."""
        if completed == 0 and cancelled == 0:
            return 3
        if completed >= 5 and cancelled == 0:
            return 5
        if completed and cancelled <= completed:
            return 4
        return 1

    @classmethod
    def get_candidates(cls, *, user, sport=None, latitude=None, longitude=None, radius=20, date=None, skill_level=None):
        now = timezone.now()
        user_sports = user.user_sports.select_related('sport').filter(sport__is_active=True)
        if sport:
            user_sports = user_sports.filter(sport=sport)
        sport_by_id = {entry.sport_id: entry for entry in user_sports}
        if not sport_by_id:
            return [], sport_by_id

        queryset = Game.objects.filter(
            visibility=Game.Visibility.PUBLIC,
            status__in=(Game.Status.OPEN, Game.Status.ALMOST_FULL),
            starts_at__gt=now,
            sport__is_active=True,
            venue__active=True,
            court__active=True,
            sport_id__in=sport_by_id,
        ).exclude(host=user).exclude(
            game_players__user=user, game_players__status=GamePlayer.Status.CONFIRMED,
        ).select_related('sport', 'venue', 'court__sport', 'host').annotate(
            confirmed_player_count=Count(
                'game_players', filter=Q(game_players__status=GamePlayer.Status.CONFIRMED), distinct=True,
            ),
        ).filter(confirmed_player_count__lt=F('max_players')).prefetch_related(
            'game_players__user__user_sports__sport',
        )
        if date:
            queryset = queryset.filter(game_date=date)
        if skill_level:
            queryset = queryset.filter(Q(skill_level='') | Q(skill_level=skill_level))
        if latitude is not None:
            queryset = nearby_queryset(
                queryset, latitude=latitude, longitude=longitude, radius=radius,
                latitude_field='venue__latitude', longitude_field='venue__longitude',
            )
        else:
            queryset = queryset.order_by('starts_at')
        return list(queryset[:MAX_CANDIDATES]), sport_by_id

    @classmethod
    def get_recommendations(cls, *, user, sport=None, latitude=None, longitude=None,
                            radius=20, date=None, skill_level=None, limit=20):
        if latitude is not None:
            latitude, longitude, radius = validate_coordinates(latitude, longitude, radius)
        candidates, user_sports = cls.get_candidates(
            user=user, sport=sport, latitude=latitude, longitude=longitude,
            radius=radius, date=date, skill_level=skill_level,
        )
        if not candidates:
            return []

        host_ids = {game.host_id for game in candidates}
        history = {
            host_id: {'completed': 0, 'cancelled': 0}
            for host_id in host_ids
        }
        for row in Game.objects.filter(host_id__in=host_ids).values('host_id', 'status').annotate(total=Count('id')):
            if row['status'] in history[row['host_id']]:
                history[row['host_id']][row['status']] = row['total']
        slots = list(AvailabilitySlot.objects.filter(user=user))
        # Older accounts may predate PlayerProfile creation. Recommendations
        # remain read-only and use neutral time scoring for those accounts.
        profile = getattr(user, 'profile', None)
        preferred_times = profile.preferred_play_times if profile else []
        profile_availability = profile.availability if profile else {}
        results = []
        for game in candidates:
            user_sport = user_sports.get(game.sport_id)
            sport_score = cls.calculate_sport_score(user_sport)
            if not sport_score:
                continue
            skill_score = cls.calculate_skill_score(user_sport.skill_level, game.skill_level)
            # A requirement more than two levels away is fundamentally incompatible.
            if game.skill_level and skill_score == 0:
                continue
            distance = getattr(game, 'distance_km', None)
            distance_score = cls.calculate_distance_score(distance)
            time_score, time_reason = cls.calculate_time_score(
                game=game, availability_slots=slots,
                preferred_times=preferred_times,
                profile_availability=profile_availability,
            )
            if slots and time_score == 0:
                continue
            available_slots = game.max_players - game.confirmed_player_count
            capacity_score = cls.calculate_capacity_score(available_slots)
            if not capacity_score:
                continue
            host_history = history[game.host_id]
            reliability_score = cls.calculate_reliability_score(
                host_history['completed'], host_history['cancelled'],
            )
            score = max(0, min(100, round(
                sport_score + skill_score + distance_score + time_score + capacity_score + reliability_score,
            )))
            reasons = ['Matches your preferred sport' if user_sport.preferred else 'Matches one of your sports']
            if skill_score >= 15:
                reasons.append('Skill level is a strong match')
            elif skill_score:
                reasons.append('Skill level is compatible')
            if distance is not None and distance_score:
                reasons.append(f'Only {distance:.1f} km away')
            if time_reason:
                reasons.append(time_reason)
            if available_slots == 1:
                reasons.append('One player slot remaining')
            elif available_slots <= 3:
                reasons.append(f'{available_slots} player slots remaining')
            results.append({
                'game': game,
                'match_score': score,
                'distance_km': round(distance, 2) if distance is not None else None,
                'reasons': reasons,
                'available_slots': available_slots,
            })
        return sorted(
            results,
            key=lambda item: (
                -item['match_score'],
                item['distance_km'] if item['distance_km'] is not None else float('inf'),
                item['game'].starts_at,
                -item['available_slots'],
            ),
        )[:limit]

"""Deterministic partner recommendations; this is not machine learning."""
from dataclasses import dataclass

from django.db.models import Count, Q
from rest_framework.exceptions import ValidationError

from my_app.models import Match, User, UserBlock
from my_app.services.locations import nearby_queryset, validate_coordinates


SPORT_WEIGHT = 30
SKILL_WEIGHT = 25
DISTANCE_WEIGHT = 20
AVAILABILITY_WEIGHT = 15
ACTIVITY_WEIGHT = 10
MAX_CANDIDATES = 500
SKILL_ORDER = {'beginner': 0, 'intermediate': 1, 'advanced': 2, 'expert': 3, 'pro': 4}
SKILL_SCORES = {0: 25, 1: 18, 2: 10, 3: 4}
DISTANCE_SCORES = ((2, 20), (5, 17), (10, 12), (20, 6))


@dataclass(frozen=True)
class MatchScore:
    score: int
    reasons: list[str]


class PlayerMatchingService:
    @staticmethod
    def _skill_score(requester_skill, candidate_skill):
        if requester_skill not in SKILL_ORDER or candidate_skill not in SKILL_ORDER:
            return 0
        return SKILL_SCORES.get(abs(SKILL_ORDER[requester_skill] - SKILL_ORDER[candidate_skill]), 0)

    @staticmethod
    def _distance_score(distance):
        for threshold, score in DISTANCE_SCORES:
            if distance <= threshold:
                return score
        return 0

    @staticmethod
    def _availability_score(requester_times, candidate_times):
        requester = {str(item).strip().lower() for item in requester_times if str(item).strip()}
        candidate = {str(item).strip().lower() for item in candidate_times if str(item).strip()}
        overlap = requester & candidate
        if not overlap:
            return 0
        return 15 if len(overlap) >= 2 or overlap == requester or overlap == candidate else 8

    @classmethod
    def recommend(cls, *, requester, sport, latitude, longitude, radius, limit):
        latitude, longitude, radius = validate_coordinates(latitude, longitude, radius)
        if not requester.user_sports.filter(sport=sport).exists():
            raise ValidationError({'sport': ['Add this sport to your profile before requesting partners.']})
        requester_sport = requester.user_sports.get(sport=sport)
        blocked_ids = UserBlock.objects.filter(user=requester).values('blocked_user_id')
        blocked_by_ids = UserBlock.objects.filter(blocked_user=requester).values('user_id')
        candidates = User.objects.filter(
            is_active=True,
            account_status=User.AccountStatus.ACTIVE,
            is_available=True,
            profile__profile_visibility='public',
            user_sports__sport=sport,
        ).exclude(pk=requester.pk).exclude(pk__in=blocked_ids).exclude(pk__in=blocked_by_ids).select_related('profile').prefetch_related('user_sports__sport')
        candidates = list(nearby_queryset(
            candidates, latitude=latitude, longitude=longitude, radius=radius,
        )[:MAX_CANDIDATES])
        candidate_ids = [candidate.pk for candidate in candidates]
        completed_counts = dict(
            Match.objects.filter(status='completed', organizer_id__in=candidate_ids)
            .values('organizer_id').annotate(total=Count('id', distinct=True))
            .values_list('organizer_id', 'total')
        )
        results = []
        for candidate in candidates:
            candidate_sport = next((entry for entry in candidate.user_sports.all() if entry.sport_id == sport.id), None)
            if candidate_sport is None:
                continue
            skill = cls._skill_score(requester_sport.skill_level, candidate_sport.skill_level)
            distance = cls._distance_score(candidate.distance_km)
            availability = cls._availability_score(
                requester.profile.preferred_play_times, candidate.profile.preferred_play_times,
            )
            activity = ACTIVITY_WEIGHT if completed_counts.get(candidate.pk, 0) else 0
            score = SPORT_WEIGHT + skill + distance + availability + activity
            reasons = ['Same sport']
            if skill:
                reasons.append('Similar skill level')
            if distance:
                reasons.append('Nearby')
            if availability:
                reasons.append('Compatible availability')
            if activity:
                reasons.append('Verified completed game activity')
            results.append({
                'candidate': candidate, 'sport': candidate_sport, 'score': MatchScore(score, reasons),
            })
        return sorted(results, key=lambda item: (-item['score'].score, item['candidate'].distance_km))[:limit]

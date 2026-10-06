"""Deterministic hybrid recommendations built from SportMate server data.

This is a recommendation orchestrator, not an external ML integration. It
uses only server-controlled profile/activity and public resource data.
"""
from django.utils import timezone

from my_app.models import Community, Event, RecommendationProfile, Tournament, Venue
from my_app.services.communities import CommunityDiscoveryService
from my_app.services.game_matching import GameMatchingService
from my_app.services.locations import nearby_queryset


class RecommendationRankingService:
    """Auditable ranking helpers shared by recommendation categories."""

    @staticmethod
    def bounded(value):
        return max(0, min(100, int(value)))

    @classmethod
    def venue_score(cls, *, supports_sport, rating, distance_km=None):
        score = (35 if supports_sport else 0) + min(45, round(float(rating or 0) * 9))
        if distance_km is not None:
            score += 20 if distance_km <= 2 else 14 if distance_km <= 5 else 7 if distance_km <= 15 else 0
        return cls.bounded(score)

    @classmethod
    def sport_resource_score(cls, *, preferred):
        return cls.bounded(60 if preferred else 40)


class RecommendationProfileService:
    """Stores a cache of server-derived preferences; clients cannot set it."""

    @staticmethod
    def profile(user):
        sports = list(user.user_sports.filter(sport__is_active=True).values('sport_id', 'preferred', 'skill_level'))
        player_profile = getattr(user, 'profile', None)
        obj, _ = RecommendationProfile.objects.get_or_create(user=user)
        obj.preferences = {
            'sports': sports,
            'city': user.city,
            'preferred_play_times': player_profile.preferred_play_times if player_profile else [],
            'preferred_distance_km': player_profile.preferred_distance if player_profile else None,
        }
        obj.last_evaluated_at = timezone.now()
        obj.save(update_fields=['preferences', 'last_evaluated_at', 'updated_at'])
        return obj


class RecommendationService:
    """Public-resource facade used by the Phase 18 API."""

    @staticmethod
    def _location(user):
        return (user.latitude, user.longitude) if user.latitude is not None and user.longitude is not None else (None, None)

    @staticmethod
    def _sports(user):
        entries = list(user.user_sports.filter(sport__is_active=True))
        return {entry.sport_id for entry in entries}, {entry.sport_id for entry in entries if entry.preferred}

    @classmethod
    def games(cls, user, limit=10):
        latitude, longitude = cls._location(user)
        return GameMatchingService.get_recommendations(user=user, latitude=latitude, longitude=longitude, radius=20, limit=limit)

    @classmethod
    def venues(cls, user, limit=20):
        latitude, longitude = cls._location(user)
        queryset = Venue.objects.filter(active=True).prefetch_related('courts__sport')
        if latitude is not None:
            queryset = nearby_queryset(queryset, latitude=latitude, longitude=longitude, radius=20)
        sports, _ = cls._sports(user)
        results = []
        for venue in queryset[:200]:
            supported = {court.sport_id for court in venue.courts.all()}
            distance = getattr(venue, 'distance_km', None)
            matches_sport = bool(supported & sports)
            reasons = []
            if matches_sport:
                reasons.append('Supports one of your sports')
            if venue.rating >= 4:
                reasons.append('Highly rated by players')
            if distance is not None and distance <= 5:
                reasons.append('Nearby')
            results.append({'venue': venue, 'score': RecommendationRankingService.venue_score(supports_sport=matches_sport, rating=venue.rating, distance_km=distance), 'distance_km': round(distance, 2) if distance is not None else None, 'reasons': reasons})
        return sorted(results, key=lambda item: (-item['score'], item['venue'].name.lower()))[:limit]

    @classmethod
    def communities(cls, user, limit=20):
        latitude, longitude = cls._location(user)
        kwargs = {'user': user, 'queryset': Community.objects.filter(visibility='public')}
        if latitude is not None:
            kwargs.update(latitude=latitude, longitude=longitude, radius=20)
        return [{'community': item, 'score': RecommendationRankingService.bounded(item.discovery_score), 'reasons': ['Matches your sports or nearby location']} for item in CommunityDiscoveryService.discover(**kwargs)[:limit]]

    @classmethod
    def events(cls, user, limit=20):
        sports, preferred = cls._sports(user)
        items = Event.objects.filter(status__in=[Event.Status.OPEN, Event.Status.FULL], visibility=Event.Visibility.PUBLIC, sport_id__in=sports).select_related('sport', 'venue').order_by('starts_at')[:limit]
        return [{'event': item, 'score': RecommendationRankingService.sport_resource_score(preferred=item.sport_id in preferred), 'reasons': ['Matches your preferred sport' if item.sport_id in preferred else 'Matches one of your sports']} for item in items]

    @classmethod
    def tournaments(cls, user, limit=20):
        sports, preferred = cls._sports(user)
        items = Tournament.objects.filter(status__in=[Tournament.Status.OPEN, Tournament.Status.FULL], sport_id__in=sports).select_related('sport', 'venue').order_by('start_date')[:limit]
        return [{'tournament': item, 'score': RecommendationRankingService.sport_resource_score(preferred=item.sport_id in preferred), 'reasons': ['Matches your preferred sport' if item.sport_id in preferred else 'Matches one of your sports']} for item in items]

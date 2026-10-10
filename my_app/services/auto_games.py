"""Deterministic, server-authoritative automatic game creation."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from my_app.models import Court, Game, UserSport, Venue
from my_app.services.availability import VenueAvailabilityService
from my_app.services.games import GameConflict, GameService
from my_app.services.locations import nearby_queryset


AUTO_GAME_DAYS_AHEAD = 7
AUTO_GAME_DURATION_MINUTES = 60


class AutoGameService:
    @staticmethod
    def _sports_for(user, sport=None):
        entries = UserSport.objects.filter(user=user, sport__is_active=True).select_related('sport')
        if sport:
            entry = entries.filter(sport=sport).first()
            if entry:
                return [entry]
            # Accounts created before the skill-profile flow have sports from
            # sign-up only. Promote the selected sport with a transparent
            # beginner default so auto-create works and can be refined later.
            if user.sports.filter(pk=sport.pk, is_active=True).exists():
                entry, _ = UserSport.objects.get_or_create(user=user, sport=sport, defaults={'skill_level': UserSport.SkillLevel.BEGINNER})
                return [entry]
            raise ValidationError({'sport_id': ['Add this sport to your player profile first.']})
        entries = list(entries.order_by('-preferred', 'sport__name'))
        if not entries:
            legacy_sports = user.sports.filter(is_active=True).order_by('name')
            entries = [UserSport.objects.get_or_create(user=user, sport=item, defaults={'skill_level': UserSport.SkillLevel.BEGINNER})[0] for item in legacy_sports]
        if not entries:
            raise ValidationError({'sport_id': ['Add at least one sport to your player profile first.']})
        return entries

    @staticmethod
    def _preferred_slot(slot, preferences):
        if not preferences:
            return True
        hour = slot['start_time'].hour
        category = 'morning' if hour < 12 else 'afternoon' if hour < 17 else 'evening' if hour < 22 else 'night'
        return category in {str(value).strip().lower() for value in preferences}

    @classmethod
    def create(cls, *, user, sport=None, radius=15):
        if user.latitude is None or user.longitude is None:
            raise ValidationError({'location': ['Enable location so SportMate can find a nearby verified court.']})
        # Avoid accidental duplicate games caused by repeated taps/retries.
        existing = Game.objects.filter(
            host=user,
            status__in=[Game.Status.OPEN, Game.Status.ALMOST_FULL],
            ends_at__gt=timezone.now(),
        ).select_related('sport', 'venue', 'court').order_by('starts_at').first()
        if existing:
            return existing, False

        preferences = getattr(getattr(user, 'profile', None), 'preferred_play_times', []) or []
        venues = nearby_queryset(
            Venue.objects.filter(active=True).prefetch_related('courts__sport'),
            latitude=user.latitude,
            longitude=user.longitude,
            radius=radius,
        )[:50]
        venues = list(venues)
        if not venues:
            raise ValidationError({'location': ['No verified active venues were found within your selected distance.']})

        for user_sport in cls._sports_for(user, sport):
            selected_sport = user_sport.sport
            for venue in venues:
                courts = [court for court in venue.courts.all() if court.active and court.sport_id == selected_sport.id]
                for day_offset in range(AUTO_GAME_DAYS_AHEAD + 1):
                    date_value = timezone.localdate() + timedelta(days=day_offset)
                    slots = VenueAvailabilityService.slots(
                        venue=venue,
                        date_value=date_value,
                        sport=selected_sport,
                        duration=AUTO_GAME_DURATION_MINUTES,
                    )
                    candidates = [slot for slot in slots if slot['court'] in courts and slot['status'] == 'available' and slot['starts_at'] > timezone.now()]
                    preferred = [slot for slot in candidates if cls._preferred_slot(slot, preferences)]
                    for slot in preferred or candidates:
                        try:
                            game = GameService.create(
                                host=user,
                                sport_id=selected_sport.id,
                                venue_id=venue.public_id,
                                court_id=slot['court'].public_id,
                                game_date=date_value,
                                start_time=slot['start_time'],
                                end_time=slot['end_time'],
                                min_players=min(2, slot['court'].capacity),
                                max_players=slot['court'].capacity,
                                skill_level=user_sport.skill_level,
                                description='Automatically created from your SportMate preferences.',
                                visibility=Game.Visibility.PUBLIC,
                            )
                            return game, True
                        except GameConflict:
                            # Another request booked this exact slot; proceed to
                            # the next candidate and preserve transactional truth.
                            continue
        raise ValidationError({'availability': ['No compatible free court slot was found in the next 7 days.']})

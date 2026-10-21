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
    # Standard match rosters. The host counts as one of these players.
    STANDARD_ROSTERS = {
        'cricket': 11,
        'badminton': 2,
        'kabaddi': 7,
        'football': 11,
        'soccer': 11,
        'hockey': 11,
        'basketball': 5,
        'volleyball': 6,
        'kho kho': 9,
        'kho-kho': 9,
        'khokho': 9,
        'tennis': 2,
        'table tennis': 2,
        'table-tennis': 2,
        'pickleball': 2,
        'carrom': 2,
        'chess': 2,
        'swimming': 1,
    }

    @classmethod
    def _roster_limits(cls, sport, court_capacity):
        configured = sport.formats.filter(is_active=True).order_by('id').first()
        if configured:
            return configured.minimum_players, configured.maximum_players
        # Compatibility for a sport added before capacity configuration is
        # migrated; configured production sports never use this fallback.
        return 2, min(2, court_capacity)

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
        selected_entries = cls._sports_for(user, sport)
        requested_sport = selected_entries[0].sport
        # Avoid accidental duplicate games caused by repeated taps/retries;
        # reuse is strictly scoped to the requested sport.
        existing = Game.objects.filter(
            host=user,
            sport=requested_sport,
            status__in=[Game.Status.OPEN, Game.Status.ALMOST_FULL],
            ends_at__gt=timezone.now(),
        ).select_related('sport', 'venue', 'court').order_by('starts_at').first()
        if existing:
            # Older auto-created games may have been made before the
            # database-backed format rules were seeded (Cricket could be
            # stored as a two-player game). Repair only an open, upcoming
            # auto-game when its current occupancy still fits the configured
            # default; historical/completed games remain unchanged.
            configured = requested_sport.formats.filter(is_active=True).order_by('id').first()
            if configured and existing.max_players < configured.default_capacity:
                confirmed = existing.game_players.filter(status='confirmed').count()
                if confirmed <= configured.default_capacity:
                    existing.max_players = configured.default_capacity
                    existing.min_players = configured.minimum_players
                    existing.save(update_fields=['max_players', 'min_players', 'updated_at'])
            return existing, False

        preferences = getattr(getattr(user, 'profile', None), 'preferred_play_times', []) or []
        venues = nearby_queryset(
            Venue.objects.filter(active=True, is_demo=False).prefetch_related('courts__sport'),
            latitude=user.latitude,
            longitude=user.longitude,
            radius=radius,
        )[:50]
        venues = list(venues)
        venues.sort(key=lambda venue: (round(float(getattr(venue, 'distance_km', 0) or 0), 6), venue.pk))
        if not venues:
            raise ValidationError({'location': ['No verified active venues were found within your selected distance.']})

        for user_sport in selected_entries:
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
                            min_players, max_players = cls._roster_limits(
                                selected_sport, slot['court'].capacity)
                            game = GameService.create(
                                host=user,
                                sport_id=selected_sport.id,
                                venue_id=venue.public_id,
                                court_id=slot['court'].public_id,
                                game_date=date_value,
                                start_time=slot['start_time'],
                                end_time=slot['end_time'],
                                min_players=min_players,
                                max_players=max_players,
                                skill_level=user_sport.skill_level,
                                description='Automatically created from your SportMate preferences.',
                                visibility=Game.Visibility.PUBLIC,
                                enforce_court_capacity=False,
                            )
                            return game, True
                        except GameConflict:
                            # Another request booked this exact slot; proceed to
                            # the next candidate and preserve transactional truth.
                            continue
        raise ValidationError({'availability': ['No compatible free court slot was found in the next 7 days.']})

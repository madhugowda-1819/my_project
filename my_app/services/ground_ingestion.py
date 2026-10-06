"""Provider-ground validation and idempotent database ingestion."""
from django.db import transaction

from my_app.maps import SUPPORTED_SPORTS
from my_app.models import Ground, Sport
from my_app.services.locations import validate_coordinates


class GroundIngestionService:
    """Turns provider results into a trusted, deduplicated Ground catalogue."""

    @staticmethod
    def _provider_key(place):
        identifier = str(place.get('id') or '').strip()
        source = str(place.get('source') or 'provider').strip().lower()
        return f'{source}:{identifier}' if identifier else None

    @staticmethod
    def _city(place, fallback_city=''):
        address = str(place.get('address') or '').strip()
        parts = [part.strip() for part in address.split(',') if part.strip()]
        # The provider has no consistently structured city field.  Prefer the
        # requested user's saved city, then a conservative address component.
        return (fallback_city or (parts[-2] if len(parts) > 1 else '')).strip()[:100]

    @staticmethod
    def _sports(place, requested_sport=None):
        text = ' '.join([
            str(place.get('name') or ''),
            ' '.join(str(item) for item in (place.get('types') or [])),
            str(requested_sport or ''),
        ]).lower()
        names = [name for name in SUPPORTED_SPORTS if name in text]
        if requested_sport:
            names.append(str(requested_sport).strip().lower())
        return Sport.objects.filter(is_active=True).filter(
            name__in=[name.title() for name in names]
        ) | Sport.objects.filter(is_active=True, slug__in=names)

    @classmethod
    @transaction.atomic
    def upsert(cls, *, place, requested_sport=None, fallback_city=''):
        try:
            latitude, longitude, _ = validate_coordinates(
                place.get('latitude'), place.get('longitude'), 1,
            )
        except Exception:
            return None, False
        key = cls._provider_key(place)
        if not key:
            return None, False
        name = str(place.get('name') or '').strip()[:200]
        if not name:
            return None, False
        defaults = {
            'name': name,
            'city': cls._city(place, fallback_city),
            'address': str(place.get('address') or '').strip()[:255],
            'latitude': latitude,
            'longitude': longitude,
            'source': str(place.get('source') or 'provider')[:30],
        }
        # Box cricket facilities are compact cricket grounds.  Persist this
        # useful size distinction without needing a provider-specific field.
        searchable_text = f"{name} {place.get('address') or ''}".lower()
        if 'box cricket' in searchable_text:
            defaults['size'] = 'small'
        ground, created = Ground.objects.get_or_create(maps_place_id=key, defaults=defaults)
        if not created:
            # Provider data may refresh names/coordinates, but never overwrite
            # staff-managed price, pitch type, or size fields.
            changed = [field for field, value in defaults.items() if getattr(ground, field) != value]
            for field in changed:
                setattr(ground, field, defaults[field])
            if changed:
                ground.save(update_fields=changed)
        sports = cls._sports(place, requested_sport)
        if sports.exists():
            ground.sports.add(*sports)
        return ground, created

    @classmethod
    def ingest(cls, *, places, requested_sport=None, fallback_city=''):
        grounds, created_count = [], 0
        for place in places:
            ground, created = cls.upsert(
                place=place,
                requested_sport=requested_sport,
                fallback_city=fallback_city,
            )
            if ground:
                grounds.append(ground)
                created_count += int(created)
        return grounds, created_count

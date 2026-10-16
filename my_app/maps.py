"""Google Places adapter. Results are live and are never stored in our DB."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache


# Only these sport facilities are part of SportMate's discovery catalogue.
# Keeping this list here also prevents generic gyms from appearing as grounds.
SUPPORTED_SPORTS = (
    'cricket', 'box cricket', 'football', 'badminton', 'basketball', 'carrom', 'chess',
    'kabaddi', 'kho kho', 'pickleball', 'swimming', 'table tennis',
    'tennis', 'volleyball',
)


class MapsProviderError(Exception):
    pass


def find_live_sports_grounds(*, latitude=None, longitude=None, sport=None,
                             radius_km=10, query=None, category=None):
    """Search current provider data without persisting/caching Google content.

    Google Places content other than place IDs is intentionally returned only
    from the live request. OSM results may use the short server cache.
    """
    api_key = settings.GOOGLE_MAPS_PLACES_API_KEY
    if not api_key:
        if latitude is None or longitude is None:
            raise MapsProviderError(
                'Location-based search needs device location when Google Places is not configured.'
            )
        results = _find_openstreetmap_sports_grounds(
            latitude=latitude, longitude=longitude, sport=sport,
            radius_km=radius_km, query=query, category=category,
        )
    else:
        query_parts = [part.strip() for part in (query, sport, category) if part and part.strip()]
        text_query = ' '.join(query_parts) or 'sports grounds, turf and sports arena'
        body = {'textQuery': text_query, 'pageSize': 20, 'languageCode': 'en'}
        if latitude is not None and longitude is not None:
            body['locationBias'] = {'circle': {
                'center': {'latitude': latitude, 'longitude': longitude},
                'radius': min(radius_km * 1000, 50000),
            }}
        request = Request(
            'https://places.googleapis.com/v1/places:searchText',
            data=json.dumps(body).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'X-Goog-Api-Key': api_key,
                'X-Goog-FieldMask': (
                    'places.id,places.displayName,places.formattedAddress,'
                    'places.location,places.types,places.googleMapsUri,places.websiteUri'
                ),
            }, method='POST',
        )
        try:
            with urlopen(request, timeout=settings.MAPS_PROVIDER_TIMEOUT_SECONDS) as response:
                try:
                    payload = json.loads(response.read().decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise MapsProviderError('Place search returned an invalid response.') from error
        except HTTPError as error:
            if latitude is not None and longitude is not None:
                try:
                    return _find_openstreetmap_sports_grounds(
                        latitude=latitude, longitude=longitude, sport=sport,
                        radius_km=radius_km, query=query, category=category,
                    )
                except MapsProviderError:
                    pass
            raise MapsProviderError(f'Place search is temporarily unavailable ({error.code}).') from error
        except (URLError, TimeoutError) as error:
            if latitude is not None and longitude is not None:
                try:
                    return _find_openstreetmap_sports_grounds(
                        latitude=latitude, longitude=longitude, sport=sport,
                        radius_km=radius_km, query=query, category=category,
                    )
                except MapsProviderError:
                    pass
            raise MapsProviderError('Place search is temporarily unavailable.') from error
        if not isinstance(payload, dict) or not isinstance(payload.get('places', []), list):
            raise MapsProviderError('Place search returned an invalid response.')
        results = []
        for place in payload.get('places', []):
            if not isinstance(place, dict):
                continue
            name = (place.get('displayName') or {}).get('text', '').strip()
            location = place.get('location') or {}
            if (not name or not place.get('id') or not isinstance(location, dict)
                    or location.get('latitude') is None or location.get('longitude') is None):
                continue
            results.append({
                'id': place.get('id'), 'name': name,
                'address': place.get('formattedAddress'),
                'latitude': location.get('latitude'), 'longitude': location.get('longitude'),
                'types': place.get('types') or [], 'mapsUrl': place.get('googleMapsUri'),
                'websiteUrl': place.get('websiteUri'), 'source': 'google_maps_live',
            })
    return results


def _find_openstreetmap_sports_grounds(*, latitude, longitude, sport=None,
                                       radius_km=10, query=None, category=None):
    """No-key fallback using OpenStreetMap's public Overpass API."""
    cache_key = 'osm-ground-search:{:.3f}:{:.3f}:{}:{}:{}:{:.1f}'.format(
        float(latitude), float(longitude), (sport or '').strip().lower(),
        (category or '').strip().lower(), (query or '').strip().lower(), float(radius_km),
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    radius_meters = int(min(radius_km * 1000, 50000))
    around = f'(around:{radius_meters},{latitude},{longitude})'
    if query:
        # Overpass regex is not a general text-search engine; use only a
        # bounded literal name match and keep the coordinate radius required.
        safe_query = _overpass_pattern(query)
        if not safe_query:
            return []
        sport_query = f'nwr{around}["name"~"{safe_query}",i];'
    elif sport:
        safe_sport = _overpass_pattern(sport.lower())
        if not safe_sport:
            return []
        sport_query = f'nwr{around}["sport"~"{safe_sport}",i]; nwr{around}["name"~"{safe_sport}",i];'
    elif category:
        leisure = {
            'turf': 'pitch', 'court': 'pitch', 'stadium': 'stadium',
            'sports centre': 'sports_centre', 'sports center': 'sports_centre',
            'sports complex': 'sports_centre',
        }.get(category.strip().lower())
        sport_query = f'nwr{around}["leisure"="{leisure}"];' if leisure else ''
    else:
        allowed = '|'.join(re.escape(value).replace(r'\ ', r'\\s*') for value in SUPPORTED_SPORTS)
        # Many small grounds (especially box-cricket venues) are mapped by
        # name and leisure type but do not carry a sport tag.
        sport_query = (
            f'nwr{around}["sport"~"^({allowed})$",i];'
            f'nwr{around}["leisure"~"pitch|sports_centre|stadium",i]["name"~"cricket|football|badminton|basketball|tennis|volleyball|pickleball",i];'
        )
    query = f'[out:json][timeout:15];({sport_query});out center tags;'
    request = Request(
        settings.OVERPASS_API_URL,
        data=urlencode({'data': query}).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'SportMate/1.0'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=settings.MAPS_PROVIDER_TIMEOUT_SECONDS) as response:
            try:
                payload = json.loads(response.read().decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MapsProviderError('OpenStreetMap returned an invalid response.') from error
    except (HTTPError, URLError, TimeoutError) as error:
        raise MapsProviderError('OpenStreetMap live-ground search is temporarily unavailable.') from error

    if not isinstance(payload, dict) or not isinstance(payload.get('elements', []), list):
        raise MapsProviderError('OpenStreetMap returned an invalid response.')
    results = []
    seen = set()
    category_leisure = {
        'turf': 'pitch', 'court': 'pitch', 'stadium': 'stadium',
        'sports centre': 'sports_centre', 'sports center': 'sports_centre',
        'sports complex': 'sports_centre',
    }.get((category or '').strip().lower())
    for element in payload.get('elements', []):
        if not isinstance(element, dict):
            continue
        element_id = f"{element.get('type')}/{element.get('id')}"
        if element_id in seen:
            continue
        seen.add(element_id)
        tags = element.get('tags', {})
        if not isinstance(tags, dict):
            continue
        center = element.get('center', element)
        item_lat, item_lng = center.get('lat'), center.get('lon')
        if item_lat is None or item_lng is None:
            continue
        metadata = ' '.join(str(tags.get(key) or '') for key in ('name', 'sport', 'sport:1', 'leisure')).replace('_', ' ').lower()
        if sport and sport.replace('_', ' ').lower() not in metadata:
            continue
        if category_leisure and tags.get('leisure') != category_leisure:
            continue
        address = ', '.join(part for part in [tags.get('addr:housenumber'), tags.get('addr:street'), tags.get('addr:city')] if part)
        name = tags.get('name')
        if not name or not str(name).strip():
            continue
        results.append({
            'id': element_id,
            'name': str(name).strip(),
            'address': address,
            'latitude': item_lat,
            'longitude': item_lng,
            'types': [tags.get('leisure', ''), tags.get('sport', '')],
            'mapsUrl': f'https://www.openstreetmap.org/{element_id}',
            'source': 'openstreetmap_live',
            'attribution': '© OpenStreetMap contributors',
        })
    cache.set(cache_key, results, settings.GROUND_SEARCH_CACHE_SECONDS)
    return results


def _overpass_pattern(value):
    safe_text = ''.join(
        char for char in str(value)[:80]
        if char.isalnum() or char in ' _-'
    ).strip()
    return re.escape(safe_text).replace(r'\ ', r'\s+')

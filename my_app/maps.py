"""Google Places adapter. Results are live and are never stored in our DB."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


# Only these sport facilities are part of SportMate's discovery catalogue.
# Keeping this list here also prevents generic gyms from appearing as grounds.
SUPPORTED_SPORTS = (
    'cricket', 'football', 'badminton', 'basketball', 'carrom', 'chess',
    'kabaddi', 'kho kho', 'pickleball', 'swimming', 'table tennis',
    'tennis', 'volleyball',
)


class MapsProviderError(Exception):
    pass


def find_live_sports_grounds(*, latitude, longitude, sport=None, radius_km=10):
    api_key = settings.GOOGLE_MAPS_PLACES_API_KEY
    if not api_key:
        return _find_openstreetmap_sports_grounds(
            latitude=latitude, longitude=longitude, sport=sport, radius_km=radius_km,
        )
    # A generic "sports ground" query also returns gyms. Request only the
    # supported activities when no sport filter has been selected.
    query = f'{sport} ground' if sport else f"{' '.join(SUPPORTED_SPORTS)} sports ground"
    body = {'textQuery': query, 'maxResultCount': 20, 'locationBias': {'circle': {'center': {'latitude': latitude, 'longitude': longitude}, 'radius': min(radius_km * 1000, 50000)}}, 'languageCode': 'en'}
    request = Request('https://places.googleapis.com/v1/places:searchText', data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json', 'X-Goog-Api-Key': api_key, 'X-Goog-FieldMask': 'places.id,places.displayName,places.formattedAddress,places.location,places.types,places.googleMapsUri'}, method='POST')
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        raise MapsProviderError(f'Google Places request failed ({error.code}).') from error
    except URLError as error:
        raise MapsProviderError('Google Places could not be reached.') from error
    return [{'id': place.get('id'), 'name': place.get('displayName', {}).get('text', 'Sports ground'), 'address': place.get('formattedAddress', ''), 'latitude': place.get('location', {}).get('latitude'), 'longitude': place.get('location', {}).get('longitude'), 'types': place.get('types', []), 'mapsUrl': place.get('googleMapsUri'), 'source': 'google_maps_live'} for place in payload.get('places', [])]


def _find_openstreetmap_sports_grounds(*, latitude, longitude, sport=None, radius_km=10):
    """No-key fallback using OpenStreetMap's public Overpass API."""
    radius_meters = int(min(radius_km * 1000, 50000))
    around = f'(around:{radius_meters},{latitude},{longitude})'
    if sport:
        safe_sport = re.escape(sport.strip().lower())
        sport_query = f'nwr{around}["sport"~"{safe_sport}",i]; nwr{around}["name"~"{safe_sport}",i];'
    else:
        allowed = '|'.join(re.escape(value).replace(r'\ ', r'\\s*') for value in SUPPORTED_SPORTS)
        sport_query = f'nwr{around}["sport"~"^({allowed})$",i];'
    query = f'[out:json][timeout:15];({sport_query});out center tags;'
    request = Request(
        settings.OVERPASS_API_URL,
        data=urlencode({'data': query}).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'SportMate/1.0'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (HTTPError, URLError, TimeoutError) as error:
        raise MapsProviderError('OpenStreetMap live-ground search is temporarily unavailable.') from error

    results = []
    seen = set()
    for element in payload.get('elements', []):
        element_id = f"{element.get('type')}/{element.get('id')}"
        if element_id in seen:
            continue
        seen.add(element_id)
        tags = element.get('tags', {})
        center = element.get('center', element)
        item_lat, item_lng = center.get('lat'), center.get('lon')
        if item_lat is None or item_lng is None:
            continue
        address = ', '.join(part for part in [tags.get('addr:housenumber'), tags.get('addr:street'), tags.get('addr:city')] if part)
        results.append({
            'id': element_id,
            'name': tags.get('name') or tags.get('sport') or 'Sports ground',
            'address': address,
            'latitude': item_lat,
            'longitude': item_lng,
            'types': [tags.get('leisure', ''), tags.get('sport', '')],
            'mapsUrl': f'https://www.openstreetmap.org/{element_id}',
            'source': 'openstreetmap_live',
            'attribution': '© OpenStreetMap contributors',
        })
    return results

"""Google Places adapter. Results are live and are never stored in our DB."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class MapsProviderError(Exception):
    pass


def find_live_sports_grounds(*, latitude, longitude, sport=None, radius_km=10):
    api_key = settings.GOOGLE_MAPS_PLACES_API_KEY
    if not api_key:
        raise MapsProviderError('Live map search is not configured. Set GOOGLE_MAPS_PLACES_API_KEY on the server.')
    query = f'{sport} ground' if sport else 'sports ground'
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

from rest_framework.exceptions import ValidationError

DEFAULT_RADIUS_KM = 15.0
MIN_RADIUS_KM = 1.0
MAX_RADIUS_KM = 100.0


def validate_radius(latitude, longitude, radius=DEFAULT_RADIUS_KM):
    try:
        lat, lng, value = float(latitude), float(longitude), float(radius)
    except (TypeError, ValueError) as exc:
        raise ValidationError('latitude, longitude, and radius must be numeric.') from exc
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValidationError('Coordinates are outside their valid range.')
    if not MIN_RADIUS_KM <= value <= MAX_RADIUS_KM:
        raise ValidationError(f'Radius must be between {MIN_RADIUS_KM:g} and {MAX_RADIUS_KM:g} km.')
    return lat, lng, value

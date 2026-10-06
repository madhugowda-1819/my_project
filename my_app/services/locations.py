"""Database-backed location and nearby-search operations.

PostgreSQL deployments can replace the bounding-box/Haversine annotations with a
PostGIS geography field and spatial index without changing the API contract.
"""
from math import cos, radians

from django.db.models import ExpressionWrapper, F, FloatField, Value
from django.db.models.functions import ASin, Cos, Power, Radians, Sin, Sqrt
from rest_framework.exceptions import ValidationError


EARTH_RADIUS_KM = 6371.0


def validate_coordinates(latitude, longitude, radius):
    try:
        latitude, longitude, radius = float(latitude), float(longitude), float(radius)
    except (TypeError, ValueError) as exc:
        raise ValidationError('latitude, longitude, and radius must be numeric.') from exc
    if not -90 <= latitude <= 90:
        raise ValidationError({'latitude': ['Latitude must be between -90 and 90.']})
    if not -180 <= longitude <= 180:
        raise ValidationError({'longitude': ['Longitude must be between -180 and 180.']})
    if not 0 < radius <= 500:
        raise ValidationError({'radius': ['Radius must be greater than 0 and at most 500 km.']})
    return latitude, longitude, radius


def nearby_queryset(queryset, *, latitude, longitude, radius, latitude_field='latitude', longitude_field='longitude'):
    """Filter and sort in SQL; bounding box keeps the Haversine calculation small."""
    latitude, longitude, radius = validate_coordinates(latitude, longitude, radius)
    latitude_delta = radius / 111.32
    longitude_delta = radius / max(111.32 * cos(radians(latitude)), 0.000001)
    queryset = queryset.filter(
        **{
            f'{latitude_field}__isnull': False,
            f'{longitude_field}__isnull': False,
            f'{latitude_field}__gte': latitude - latitude_delta,
            f'{latitude_field}__lte': latitude + latitude_delta,
            f'{longitude_field}__gte': longitude - longitude_delta,
            f'{longitude_field}__lte': longitude + longitude_delta,
        },
    )

    latitude_delta_rad = Radians(F(latitude_field) - Value(latitude))
    longitude_delta_rad = Radians(F(longitude_field) - Value(longitude))
    origin_latitude_rad = Radians(Value(latitude))
    candidate_latitude_rad = Radians(F(latitude_field))
    haversine_a = (
        Power(Sin(latitude_delta_rad / Value(2.0)), Value(2.0))
        + Cos(origin_latitude_rad) * Cos(candidate_latitude_rad)
        * Power(Sin(longitude_delta_rad / Value(2.0)), Value(2.0))
    )
    distance = ExpressionWrapper(
        Value(2.0 * EARTH_RADIUS_KM) * ASin(Sqrt(haversine_a)),
        output_field=FloatField(),
    )
    return queryset.annotate(distance_km=distance).filter(distance_km__lte=radius).order_by('distance_km')

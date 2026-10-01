from math import atan2, cos, radians, sin, sqrt

from django.db.models import Count, F

from my_app.models import Match


def _distance_km(lat1, lng1, lat2, lng2):
    radius = 6371.0
    lat_delta = radians(lat2 - lat1)
    lng_delta = radians(lng2 - lng1)
    a = sin(lat_delta / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(lng_delta / 2) ** 2
    return radius * 2 * atan2(sqrt(a), sqrt(1 - a))


def recommended_matches_for(user, radius_km=10, limit=100):
    matches = (
        Match.objects.select_related('sport', 'ground', 'organizer')
        .annotate(joined_count=Count('joined_players'))
        .filter(status='upcoming', joined_count__lt=F('total_players'))
        .order_by('date_time')
    )
    if user.latitude is None or user.longitude is None:
        return list(matches[:limit])

    nearby = []
    for match in matches:
        distance = _distance_km(user.latitude, user.longitude, match.ground.latitude, match.ground.longitude)
        if distance <= radius_km:
            match.distance_km = round(distance, 2)
            nearby.append(match)
    return sorted(nearby, key=lambda match: (match.distance_km, match.date_time))[:limit]

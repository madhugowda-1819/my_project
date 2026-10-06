from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from my_app.models import CourtBooking, VenueReview
from my_app.services.locations import nearby_queryset


RANKING_WEIGHTS = {'rating': 50, 'distance': 25, 'price': 15, 'availability': 10}
VALID_AMENITIES = {
    'parking', 'washroom', 'changing_room', 'drinking_water', 'equipment',
    'lighting', 'air_conditioning', 'seating',
}


class VenueDiscoveryService:
    @staticmethod
    def filter_and_rank(*, queryset, data, amenities):
        queryset = queryset.filter(active=True).prefetch_related(
            'venue_sports__sport', 'courts__sport', 'images', 'reviews', 'amenities',
        )
        if data.get('sport'):
            sport = str(data['sport'])
            queryset = queryset.filter(
                Q(venue_sports__sport__slug__iexact=sport)
                | Q(venue_sports__sport__name__iexact=sport)
                | Q(venue_sports__sport__pk=sport if sport.isdigit() else -1)
            ).distinct()
        if data.get('city'):
            queryset = queryset.filter(city__iexact=data['city'].strip())
        if data.get('min_rating') is not None:
            queryset = queryset.filter(rating__gte=data['min_rating'])
        if data.get('max_price') is not None:
            queryset = queryset.filter(courts__active=True, courts__price_per_hour__lte=data['max_price']).distinct()
        invalid = set(amenities) - VALID_AMENITIES
        if invalid:
            raise ValidationError({'amenities': [f'Unsupported amenity: {item}' for item in sorted(invalid)]})
        for amenity in amenities:
            queryset = queryset.filter(**{f'amenities__{amenity}': True})
        if data.get('latitude') is not None and data.get('longitude') is not None:
            queryset = nearby_queryset(queryset, latitude=data['latitude'], longitude=data['longitude'], radius=data['radius'])
            venues = list(queryset)
        else:
            venues = list(queryset.order_by('-rating', 'name'))
        for venue in venues:
            distance_score = max(0, RANKING_WEIGHTS['distance'] - getattr(venue, 'distance_km', 0))
            prices = [court.price_per_hour for court in venue.courts.all() if court.active]
            price_score = RANKING_WEIGHTS['price'] if prices and min(prices) <= 500 else 0
            availability_score = RANKING_WEIGHTS['availability'] if any(court.active for court in venue.courts.all()) else 0
            venue.discovery_score = float(venue.rating) * 10 + distance_score + price_score + availability_score
        return sorted(venues, key=lambda venue: (-venue.discovery_score, getattr(venue, 'distance_km', float('inf'))))


class VenueReviewService:
    @staticmethod
    def create(*, user, booking, rating, comment=''):
        if booking.user_id != user.id:
            raise PermissionDenied('You may only review your own booking.')
        if booking.status != CourtBooking.Status.COMPLETED:
            raise ValidationError({'booking': ['Only completed bookings can be reviewed.']})
        if hasattr(booking, 'review'):
            raise ValidationError({'booking': ['This booking has already been reviewed.']})
        return VenueReview.objects.create(venue=booking.court.venue, user=user, booking=booking, rating=rating, comment=comment)

    @staticmethod
    def delete(*, user, review):
        if review.user_id != user.id:
            raise PermissionDenied('You may only delete your own review.')
        review.delete()

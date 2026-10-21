from rest_framework.exceptions import ValidationError

from my_app.models import Ground, Sport, Venue


def validate_approved_ground(*, ground, sport):
    """Validate a Ground for new Match creation; legacy rows remain readable."""
    if not isinstance(ground, Ground):
        raise ValidationError({'ground': ['Use a valid internal Ground ID.']})
    venue = getattr(ground, 'verified_venue', None)
    if venue is None or not venue.active or venue.is_demo:
        raise ValidationError({'ground': ['This ground is not linked to an approved active venue.']})
    if not (venue.venue_sports.filter(sport=sport).exists() or
            venue.courts.filter(sport=sport, active=True).exists()):
        raise ValidationError({'ground': ['The selected venue does not support this sport.']})
    if ground.latitude is None or ground.longitude is None:
        raise ValidationError({'ground': ['The approved venue has no valid location.']})
    return venue


def validate_approved_venue(*, venue, sport):
    if not isinstance(venue, Venue) or not venue.active or venue.is_demo:
        raise ValidationError({'venue': ['Choose an approved active venue.']})
    if not (venue.venue_sports.filter(sport=sport).exists() or
            venue.courts.filter(sport=sport, active=True).exists()):
        raise ValidationError({'venue': ['The selected venue does not support this sport.']})
    if venue.latitude is None or venue.longitude is None:
        raise ValidationError({'venue': ['The approved venue has no valid location.']})
    return venue

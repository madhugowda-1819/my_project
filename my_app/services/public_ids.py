from django.db import transaction
from django.utils import timezone

from my_app.models import PublicIdCounter

_PREFIX = {PublicIdCounter.Kind.PLAYER: 'P', PublicIdCounter.Kind.VENUE_OWNER: 'VO'}


def assign_user_public_id(user, *, kind):
    field = 'player_id' if kind == PublicIdCounter.Kind.PLAYER else 'venue_owner_id'
    if getattr(user, field):
        return getattr(user, field)
    with transaction.atomic():
        counter, _ = PublicIdCounter.objects.get_or_create(kind=kind, defaults={'next_value': 1})
        counter = PublicIdCounter.objects.select_for_update().get(pk=counter.pk)
        value = counter.next_value
        counter.next_value = value + 1
        counter.save(update_fields=['next_value', 'updated_at'])
    registration_date = user.created_at or timezone.now()
    public_id = f'SM{registration_date.year}{_PREFIX[kind]}{value:02d}'
    setattr(user, field, public_id)
    user.save(update_fields=[field, 'updated_at'])
    return public_id

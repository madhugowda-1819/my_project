from django.db import transaction
from rest_framework.exceptions import ValidationError

from my_app.models import UserSport


class PlayerService:
    @staticmethod
    @transaction.atomic
    def replace_sports(*, user, sports):
        sport_ids = [item['sport'].pk for item in sports]
        if len(sport_ids) != len(set(sport_ids)):
            raise ValidationError({'sports': ['A sport can only be added once.']})

        existing = {entry.sport_id: entry for entry in UserSport.objects.select_for_update().filter(user=user)}
        requested_ids = set(sport_ids)
        UserSport.objects.filter(user=user).exclude(sport_id__in=requested_ids).delete()

        for item in sports:
            sport = item['sport']
            defaults = {
                'skill_level': item['skill_level'],
                'rating': item['rating'],
                'preferred': item['preferred'],
            }
            entry = existing.get(sport.pk)
            if entry:
                for field, value in defaults.items():
                    setattr(entry, field, value)
                entry.save(update_fields=[*defaults.keys(), 'updated_at'])
            else:
                UserSport.objects.create(user=user, sport=sport, **defaults)

        # Retain legacy relation while all reads move to UserSport.
        user.sports.set(requested_ids)
        return UserSport.objects.filter(user=user).select_related('sport').order_by('sport__name')

    @staticmethod
    def update_profile(*, user, validated_data):
        profile, _ = user.profile.__class__.objects.get_or_create(user=user)
        for field, value in validated_data.items():
            setattr(profile, field, value)
        profile.save()
        if 'bio' in validated_data:
            user.bio = validated_data['bio']
            user.save(update_fields=['bio', 'updated_at'])
        return profile

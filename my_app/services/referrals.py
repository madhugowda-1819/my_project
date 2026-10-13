import secrets

from django.db import IntegrityError, transaction
from rest_framework.exceptions import PermissionDenied, ValidationError

from my_app.models import PlayerProfile, ReferralAttribution, User
from my_app.services.notifications import NotificationService


class ReferralService:
    """Creates referral codes and records qualifying player registrations."""

    @staticmethod
    def ensure_code(user):
        if user.referral_code:
            return user.referral_code
        for _ in range(8):
            code = secrets.token_hex(8).upper()
            try:
                with transaction.atomic():
                    changed = User.objects.filter(pk=user.pk, referral_code__isnull=True).update(referral_code=code)
                user.refresh_from_db(fields=['referral_code'])
                if user.referral_code:
                    return user.referral_code
                if changed:
                    return code
            except IntegrityError:
                continue
        raise ValidationError({'referral_code': ['A referral code could not be created. Please try again.']})

    @staticmethod
    def resolve_referrer(code, *, email=None):
        normalized = (code or '').strip().upper()
        if not normalized:
            return None
        referrer = User.objects.select_for_update().filter(referral_code__iexact=normalized).first()
        if (referrer is None or not referrer.is_active
                or referrer.account_status != User.AccountStatus.ACTIVE
                or not PlayerProfile.objects.filter(user=referrer).exists()):
            raise ValidationError({'referral_code': ['This referral code is invalid or unavailable.']})
        if email and referrer.email.strip().casefold() == email.strip().casefold():
            raise ValidationError({'referral_code': ['You cannot refer your own account.']})
        return referrer

    @classmethod
    def record_successful_registration(cls, *, referrer, referred_user, code):
        if referrer.pk == referred_user.pk:
            raise ValidationError({'referral_code': ['You cannot refer your own account.']})
        if not PlayerProfile.objects.filter(user=referred_user).exists():
            raise ValidationError({'referral_code': ['Only player registrations qualify for referrals.']})
        attribution = ReferralAttribution.objects.create(
            referrer=referrer,
            referred_user=referred_user,
            referral_code=code.strip().upper(),
            status=ReferralAttribution.Status.SUCCESSFUL,
        )
        NotificationService.create_notification(
            user=referrer,
            actor=None,
            type='referral',
            title='A friend joined SportMate',
            body='A new player completed registration with your referral code.',
            data={'status': ReferralAttribution.Status.SUCCESSFUL},
            event_key=f'referral:{attribution.public_id}',
        )
        return attribution

    @classmethod
    def private_summary(cls, user):
        if not PlayerProfile.objects.filter(user=user).exists():
            raise PermissionDenied('Referrals are available to player accounts only.')
        code = cls.ensure_code(user)
        referrals = ReferralAttribution.objects.filter(referrer=user)
        total = referrals.count()
        successful = referrals.filter(
            status=ReferralAttribution.Status.SUCCESSFUL,
            referred_user__is_active=True,
            referred_user__account_status=User.AccountStatus.ACTIVE,
            referred_user__profile__isnull=False,
        ).count()
        return {'referral_code': code, 'total_referrals': total, 'successful_registrations': successful}

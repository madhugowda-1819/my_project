from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, ValidationError
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from my_app.models import PlayerProfile, User, UserSport


class AccountUnavailable(PermissionDenied):
    default_code = 'ACCOUNT_UNAVAILABLE'
    default_detail = 'This account is suspended or deactivated.'


class AuthenticationService:
    @staticmethod
    def register(*, data):
        email = data['email'].strip().lower()
        username = data['username'].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError({'email': ['An account with this email already exists.']})
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError({'username': ['An account with this username already exists.']})

        candidate = User(email=email, username=username, full_name=data['full_name'])
        try:
            validate_password(data['password'], user=candidate)
        except DjangoValidationError as exc:
            raise ValidationError({'password': list(exc.messages)}) from exc

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    username=username,
                    full_name=data['full_name'].strip(),
                    phone=data.get('phone', '').strip(),
                    city=data.get('city', '').strip(),
                    password=data['password'],
                )
                PlayerProfile.objects.create(user=user)
                sports = data.get('sports', [])
                if sports:
                    user.sports.set(sports)
                    UserSport.objects.bulk_create([
                        UserSport(user=user, sport=sport, preferred=index == 0)
                        for index, sport in enumerate(sports)
                    ])
                return user
        except IntegrityError as exc:
            raise ValidationError({'email': ['An account with these details already exists.']}) from exc

    @staticmethod
    def login(*, identifier, password):
        normalized = identifier.strip()
        user = User.objects.filter(
            Q(email__iexact=normalized) | Q(username__iexact=normalized)
        ).first()
        if user is None:
            if '@' in normalized:
                raise AuthenticationFailed('No account exists with this email address.')
            raise AuthenticationFailed('Username not found.')
        if not user.check_password(password):
            raise AuthenticationFailed('Incorrect password.')
        if not user.is_active or user.account_status != User.AccountStatus.ACTIVE:
            raise AccountUnavailable()

        user.last_login = timezone.now()
        user.is_online = True
        user.save(update_fields=['last_login', 'is_online', 'last_seen', 'updated_at'])
        return user

    @staticmethod
    def tokens_for(user):
        refresh = RefreshToken.for_user(user)
        return {'access': str(refresh.access_token), 'refresh': str(refresh)}

    @staticmethod
    def logout(*, user, refresh_token):
        if not refresh_token:
            raise ValidationError({'refresh': ['A refresh token is required.']})
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError as exc:
            raise ValidationError({'refresh': ['Invalid or expired refresh token.']}) from exc
        user.is_online = False
        user.save(update_fields=['is_online', 'last_seen', 'updated_at'])

    @staticmethod
    def set_password(*, user, password):
        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            raise ValidationError({'password': list(exc.messages)}) from exc
        user.set_password(password)
        user.save(update_fields=['password', 'last_seen', 'updated_at'])


class UserService:
    EDITABLE_PROFILE_FIELDS = {'full_name', 'phone', 'avatar', 'bio', 'city', 'latitude', 'longitude', 'is_available'}

    @staticmethod
    def update_profile(*, user, validated_data):
        for field, value in validated_data.items():
            if field in UserService.EDITABLE_PROFILE_FIELDS:
                setattr(user, field, value)
        user.save(update_fields=[field for field in validated_data if field in UserService.EDITABLE_PROFILE_FIELDS] + ['updated_at'])
        return user

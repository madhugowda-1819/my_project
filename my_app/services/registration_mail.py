from django.conf import settings
from django.core.mail import send_mail


def send_player_welcome(user):
    if not user.email or not user.player_id:
        return
    send_mail(
        subject='Welcome to SportMate',
        message=f'Welcome to SportMate, {user.full_name or user.username}!\n\nYour permanent Player ID is {user.player_id}. Keep it safe; use it when joining tournament teams.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )


def send_venue_owner_registration(user):
    if not user.email or not user.venue_owner_id:
        return
    send_mail(
        subject='SportMate venue-owner application received',
        message=f'Your venue-owner application was received.\n\nVenue Owner ID: {user.venue_owner_id}\n\nYour application is pending administrator approval.',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )

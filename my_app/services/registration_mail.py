import hashlib
import secrets
from datetime import timedelta
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils import timezone

from my_app.models import EmailVerificationToken


def _email(subject, recipient, heading, text, details=None, action_label=None, action_url=None):
    if not recipient:
        return False
    details = details or []
    rows = ''.join(f'<tr><td style="padding:8px 0;color:#657899">{escape(str(k))}</td><td style="padding:8px 0;text-align:right;font-weight:600">{escape(str(v))}</td></tr>' for k, v in details)
    button = ''
    if action_label and action_url:
        url = escape(action_url, quote=True)
        button = f'<p><a href="{url}" style="background:#0966F8;color:#fff;text-decoration:none;padding:14px 22px;border-radius:10px;font-weight:700;display:inline-block">{escape(action_label)}</a></p><p style="font-size:12px;color:#657899;word-break:break-all">If the button does not work, copy this link:<br>{url}</p>'
    table = f'<table width="100%" style="margin-top:22px;background:#EAF4FF;border-radius:12px;padding:10px">{rows}</table>' if rows else ''
    html = f'<!doctype html><html><body style="margin:0;background:#F4F9FF;font-family:Arial,sans-serif;color:#101D3C"><table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 8px"><tr><td align="center"><table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#fff;border-radius:18px;overflow:hidden"><tr><td style="background:#0966F8;padding:24px;color:#fff;font-size:24px;font-weight:800">Sport<span style="color:#B8FF27">Mate</span></td></tr><tr><td style="padding:32px 28px"><h1>{escape(heading)}</h1><div style="font-size:16px;line-height:1.6;white-space:pre-line">{escape(text)}</div>{table}{button}<p style="color:#657899">Regards,<br><strong>Team SportMate</strong></p></td></tr><tr><td style="padding:18px 28px;background:#F4F9FF;color:#657899;font-size:12px">Automated SportMate email.</td></tr></table></td></tr></table></body></html>'
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [recipient])
    message.attach_alternative(html, 'text/html')
    message.send(fail_silently=True)
    return True


def issue_email_verification(user):
    EmailVerificationToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    raw = secrets.token_urlsafe(32)
    EmailVerificationToken.objects.create(user=user, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=timezone.now() + timedelta(hours=settings.EMAIL_VERIFICATION_TTL_HOURS))
    base = settings.EMAIL_VERIFICATION_URL.rstrip('/')
    link = f'{base}{reverse("email-verification", kwargs={"token": raw})}' if base else reverse('email-verification', kwargs={'token': raw})
    name = user.full_name or user.username
    return _email('SportMate – Verify Your Email Address', user.email, 'Welcome to SportMate!', f'Hello {name},\n\nThank you for joining SportMate. Please verify your email address to activate your account.\n\nThis link expires in {settings.EMAIL_VERIFICATION_TTL_HOURS} hours.', [('Player ID', user.player_id or '—'), ('Registered email', user.email)], 'Verify Email Address', link)


def send_player_welcome(user):
    if not user.email or not user.player_id:
        return False
    return _email('Welcome to SportMate – Your Account Is Ready!', user.email, "You're officially part of SportMate!", f'Hello {user.full_name or user.username},\n\nYour email has been verified and your SportMate account is now active.', [('Player name', user.full_name or user.username), ('Player ID', user.player_id), ('Account status', 'Active')])


def send_venue_owner_registration(user):
    if not user.email or not user.venue_owner_id:
        return False
    return _email('SportMate – Venue Owner Registration Received', user.email, 'Welcome to SportMate, Venue Owner!', f'Hello {user.full_name or user.username},\n\nYour venue-owner registration was received successfully. Your application is pending administrator approval.', [('Venue Owner ID', user.venue_owner_id), ('Registered email', user.email), ('Status', 'Pending review')])

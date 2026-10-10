"""Razorpay order creation and verified booking-payment transitions."""
import hashlib
import hmac
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from my_app.models import CourtBooking, PaymentTransaction
from my_app.services.bookings import BookingService, BookingStatusService


class PaymentError(APIException):
    status_code = 503
    default_code = 'PAYMENT_UNAVAILABLE'
    default_detail = 'Payments are temporarily unavailable. Please try again.'


class PaymentService:
    api_url = 'https://api.razorpay.com/v1'

    @staticmethod
    def _credentials():
        if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
            raise PaymentError('Payments have not been configured.')
        return settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET

    @staticmethod
    def _paise(amount):
        return int((Decimal(amount) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    @classmethod
    def start(cls, *, user, booking_data):
        key_id, secret = cls._credentials()
        with transaction.atomic():
            booking = BookingService.create(user=user, status=CourtBooking.Status.PENDING, **booking_data)
            amount_paise = cls._paise(booking.final_amount)
        try:
            response = requests.post(
                f'{cls.api_url}/orders', auth=(key_id, secret), timeout=15,
                json={'amount': amount_paise, 'currency': 'INR', 'receipt': booking.booking_reference, 'notes': {'booking_id': str(booking.public_id)}},
            )
            response.raise_for_status()
            order = response.json()
        except (requests.RequestException, ValueError) as exc:
            booking.status = CourtBooking.Status.CANCELLED
            booking.cancellation_reason = 'Payment order could not be created.'
            booking.save(update_fields=['status', 'cancellation_reason', 'updated_at'])
            raise PaymentError() from exc
        payment = PaymentTransaction.objects.create(booking=booking, provider_order_id=order['id'], amount_paise=amount_paise)
        return payment, key_id

    @classmethod
    @transaction.atomic
    def verify_checkout(cls, *, user, provider_order_id, provider_payment_id, signature):
        payment = PaymentTransaction.objects.select_for_update().select_related('booking').get(provider_order_id=provider_order_id, booking__user=user)
        expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), f'{payment.provider_order_id}|{provider_payment_id}'.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValidationError({'payment': ['Payment signature is invalid.']})
        payment.provider_payment_id = provider_payment_id
        payment.status = PaymentTransaction.Status.AUTHORIZED
        payment.save(update_fields=['provider_payment_id', 'status', 'updated_at'])
        # The webhook remains the source of truth for delayed events. Fetching
        # here gives the customer an immediate confirmed result when auto-capture
        # has already completed.
        _, secret = cls._credentials()
        try:
            response = requests.get(f'{cls.api_url}/payments/{provider_payment_id}', auth=(settings.RAZORPAY_KEY_ID, secret), timeout=15)
            response.raise_for_status()
            remote = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PaymentError('Payment is being processed. Please check your bookings shortly.') from exc
        if remote.get('order_id') != payment.provider_order_id or remote.get('amount') != payment.amount_paise:
            raise ValidationError({'payment': ['Payment details do not match this order.']})
        if remote.get('status') == 'captured':
            return cls.capture(provider_order_id=payment.provider_order_id, provider_payment_id=provider_payment_id)
        return payment

    @classmethod
    @transaction.atomic
    def capture(cls, *, provider_order_id, provider_payment_id=None):
        payment = PaymentTransaction.objects.select_for_update().select_related('booking').get(provider_order_id=provider_order_id)
        if payment.status == PaymentTransaction.Status.CAPTURED:
            return payment
        if provider_payment_id:
            payment.provider_payment_id = provider_payment_id
        payment.status = PaymentTransaction.Status.CAPTURED
        payment.captured_at = timezone.now()
        payment.save(update_fields=['provider_payment_id', 'status', 'captured_at', 'updated_at'])
        if payment.booking.status == CourtBooking.Status.PENDING:
            BookingStatusService.transition(booking=payment.booking, target_status=CourtBooking.Status.CONFIRMED)
        return payment

    @classmethod
    @transaction.atomic
    def fail(cls, *, provider_order_id, reason=''):
        payment = PaymentTransaction.objects.select_for_update().select_related('booking').get(provider_order_id=provider_order_id)
        if payment.status == PaymentTransaction.Status.CAPTURED:
            return payment
        payment.status = PaymentTransaction.Status.FAILED
        payment.failure_reason = reason[:255]
        payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
        if payment.booking.status == CourtBooking.Status.PENDING:
            payment.booking.status = CourtBooking.Status.CANCELLED
            payment.booking.cancellation_reason = 'Payment failed.'
            payment.booking.cancelled_at = timezone.now()
            payment.booking.save(update_fields=['status', 'cancellation_reason', 'cancelled_at', 'updated_at'])
        return payment

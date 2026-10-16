from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from my_app.models import HealthActivityRecord


METRIC_UNITS = {
    HealthActivityRecord.Metric.STEPS: 'steps',
    HealthActivityRecord.Metric.DURATION: 'minutes',
    HealthActivityRecord.Metric.CALORIES: 'kcal',
}


class HealthActivityService:
    @staticmethod
    def _validate_date(recorded_date, timezone_name):
        try:
            local_today = timezone.now().astimezone(ZoneInfo(timezone_name)).date()
        except (ZoneInfoNotFoundError, TypeError):
            raise ValidationError({'timezone': ['Use a valid IANA timezone.']})
        if recorded_date > local_today:
            raise ValidationError({'recorded_date': ['Activity date cannot be in the future.']})
        if recorded_date < local_today - timedelta(days=365):
            raise ValidationError({'recorded_date': ['Activity date cannot be more than one year ago.']})

    @staticmethod
    @transaction.atomic
    def sync_import(*, user, source, records):
        now = timezone.now()
        created = updated = 0
        for item in records:
            metric = item['metric']
            value = item['value']
            if metric == HealthActivityRecord.Metric.STEPS and Decimal(value) != Decimal(value).to_integral_value():
                raise ValidationError({'records': ['Steps must be whole numbers.']})
            maximum = 10000 if metric == HealthActivityRecord.Metric.CALORIES else 1440 if metric == HealthActivityRecord.Metric.DURATION else 200000
            if Decimal(value) > maximum:
                raise ValidationError({'records': [f'{metric} value exceeds the supported daily maximum.']})
            recorded_date = item['recorded_date']
            try:
                timezone_name = item.get('timezone', 'Asia/Kolkata')
                local_today = timezone.now().astimezone(ZoneInfo(timezone_name)).date()
            except (ZoneInfoNotFoundError, TypeError):
                raise ValidationError({'records': ['Every imported record must use a valid IANA timezone.']})
            if recorded_date > local_today or recorded_date < local_today - timedelta(days=365):
                raise ValidationError({'records': ['Imported records must be within the last 365 days and not in the future in their recorded timezone.']})
            _, was_created = HealthActivityRecord.objects.update_or_create(
                user=user, source=source, external_record_id=item['external_record_id'],
                defaults={
                    'metric': metric, 'value': value, 'unit': METRIC_UNITS[metric],
                    'recorded_date': recorded_date, 'synced_at': now,
                    'recorded_timezone': timezone_name,
                    'is_estimated': False,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        return {'created': created, 'updated': updated}

    @staticmethod
    def create_manual(*, user, metric, value, recorded_date, timezone_name='Asia/Kolkata'):
        unit = METRIC_UNITS[metric]
        if metric == HealthActivityRecord.Metric.STEPS and Decimal(value) != Decimal(value).to_integral_value():
            raise ValidationError({'value': ['Steps must be a whole number.']})
        if metric == HealthActivityRecord.Metric.CALORIES and Decimal(value) > 10000:
            raise ValidationError({'value': ['Calories must be at most 10000 kcal per entry.']})
        if metric == HealthActivityRecord.Metric.DURATION and Decimal(value) > 1440:
            raise ValidationError({'value': ['Activity duration must be at most 1440 minutes per entry.']})
        HealthActivityService._validate_date(recorded_date, timezone_name)
        return HealthActivityRecord.objects.create(
            user=user, metric=metric, value=value, unit=unit,
            recorded_date=recorded_date, recorded_timezone=timezone_name, source=HealthActivityRecord.Source.MANUAL,
            synced_at=timezone.now(), is_estimated=metric == HealthActivityRecord.Metric.CALORIES,
        )

    @staticmethod
    def serialize_record(record):
        return {
            'public_id': str(record.public_id), 'metric': record.metric,
            'value': float(record.value), 'unit': record.unit,
            'recorded_date': record.recorded_date.isoformat(),
            'timezone': record.recorded_timezone,
            'source': record.source, 'source_label': record.get_source_display(),
            'synced_at': record.synced_at.isoformat(),
            'external_record_id': record.external_record_id or None,
            'is_estimated': record.is_estimated,
        }

    @classmethod
    def summary(cls, *, user, start, end, timezone_name='Asia/Kolkata'):
        try:
            local_today = timezone.now().astimezone(ZoneInfo(timezone_name)).date()
        except (ZoneInfoNotFoundError, TypeError):
            raise ValidationError({'timezone': ['Use a valid IANA timezone.']})
        if start > end or end > local_today or (end - start).days > 365:
            raise ValidationError({'date': ['Use a valid past or current date range of at most 366 days.']})
        rows = HealthActivityRecord.objects.filter(user=user, recorded_date__range=(start, end))
        records = [cls.serialize_record(record) for record in rows.order_by('-recorded_date', '-synced_at')]
        totals = {}
        for metric, unit in METRIC_UNITS.items():
            totals[metric] = {
                'value': float(rows.filter(metric=metric).aggregate(total=Sum('value'))['total'] or 0),
                'unit': unit,
            }
        by_date = {}
        for record in rows.values('recorded_date', 'metric').annotate(total=Sum('value')):
            key = record['recorded_date'].isoformat()
            by_date.setdefault(key, {})[record['metric']] = float(record['total'])
        available_sources = list(HealthActivityRecord.objects.filter(
            user=user,
        ).exclude(source=HealthActivityRecord.Source.MANUAL).values_list('source', flat=True).distinct())
        return {'start_date': start.isoformat(), 'end_date': end.isoformat(), 'totals': totals, 'days': by_date, 'records': records, 'available_sources': available_sources}

    @staticmethod
    @transaction.atomic
    def delete_record(*, user, public_id):
        deleted, _ = HealthActivityRecord.objects.filter(user=user, public_id=public_id, source=HealthActivityRecord.Source.MANUAL).delete()
        return bool(deleted)

    @staticmethod
    @transaction.atomic
    def disconnect_source(*, user, source):
        if source == HealthActivityRecord.Source.MANUAL:
            raise ValidationError({'source': ['Manual records are removed individually.']})
        deleted, _ = HealthActivityRecord.objects.filter(user=user, source=source).delete()
        return deleted

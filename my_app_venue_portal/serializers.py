"""Venue-owner input serializers; response serializers remain shared."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.validators import RegexValidator
from django.utils import timezone
from rest_framework import serializers

from my_app.models import Court, CourtBlockedPeriod, Ground, Sport, Venue, VenueSport

phone_validator = RegexValidator(regex=r'^\+?[0-9 ()-]{7,20}$', message='Enter a valid phone number.')


class VenueOwnerVenueWriteSerializer(serializers.ModelSerializer):
    sport_ids = serializers.PrimaryKeyRelatedField(source='sports', many=True, queryset=Sport.objects.filter(is_active=True), required=False)
    maps_place_id = serializers.CharField(required=False, allow_blank=True, write_only=True, max_length=255)

    class Meta:
        model = Venue
        fields = ['name', 'description', 'address', 'city', 'latitude', 'longitude', 'phone', 'email', 'opening_time', 'closing_time', 'timezone', 'sport_ids', 'maps_place_id']

    def validate(self, attrs):
        place_id = attrs.get('maps_place_id', '').strip()
        if place_id:
            existing_ground = Ground.objects.filter(maps_place_id=place_id).select_related('verified_venue').first()
            if existing_ground and existing_ground.verified_venue and (
                not self.instance or existing_ground.verified_venue.pk != self.instance.pk
            ):
                raise serializers.ValidationError({'maps_place_id': 'This Google Maps place is already linked to another SportMate venue.'})
        opening = attrs.get('opening_time', getattr(self.instance, 'opening_time', None))
        closing = attrs.get('closing_time', getattr(self.instance, 'closing_time', None))
        if opening is not None and closing is not None and closing <= opening:
            raise serializers.ValidationError({'closing_time': 'Closing time must be after opening time.'})
        zone = attrs.get('timezone', getattr(self.instance, 'timezone', 'Asia/Kolkata'))
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, TypeError):
            raise serializers.ValidationError({'timezone': 'Use a valid IANA timezone.'})
        return attrs

    def create(self, validated_data):
        sports = validated_data.pop('sports', [])
        place_id = validated_data.pop('maps_place_id', '').strip()
        source_ground = Ground.objects.filter(maps_place_id=place_id).first() if place_id else None
        if place_id and source_ground is None:
            source_ground = Ground.objects.create(
                name=validated_data['name'], city=validated_data['city'], address=validated_data['address'],
                latitude=validated_data['latitude'], longitude=validated_data['longitude'],
                maps_place_id=place_id, source='google_maps_live',
            )
        if source_ground:
            validated_data['source_ground'] = source_ground
        venue = Venue.objects.create(**validated_data)
        VenueSport.objects.bulk_create([VenueSport(venue=venue, sport=sport) for sport in sports])
        return venue

    def update(self, instance, validated_data):
        sports = validated_data.pop('sports', None)
        place_id = validated_data.pop('maps_place_id', None)
        if place_id is not None:
            place_id = place_id.strip()
            source_ground = Ground.objects.filter(maps_place_id=place_id).first() if place_id else None
            if place_id and source_ground is None:
                source_ground = Ground.objects.create(
                    name=validated_data.get('name', instance.name), city=validated_data.get('city', instance.city),
                    address=validated_data.get('address', instance.address), latitude=validated_data.get('latitude', instance.latitude),
                    longitude=validated_data.get('longitude', instance.longitude), maps_place_id=place_id, source='google_maps_live',
                )
            validated_data['source_ground'] = source_ground
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if sports is not None:
            VenueSport.objects.filter(venue=instance).delete()
            VenueSport.objects.bulk_create([VenueSport(venue=instance, sport=sport) for sport in sports])
        return instance


class VenueOwnerCourtWriteSerializer(serializers.ModelSerializer):
    sport_id = serializers.PrimaryKeyRelatedField(source='sport', queryset=Sport.objects.filter(is_active=True))

    class Meta:
        model = Court
        fields = ['name', 'sport_id', 'capacity', 'price_per_hour']

    def validate_price_per_hour(self, value):
        if value < 0:
            raise serializers.ValidationError('Price cannot be negative.')
        return value


class VenueOwnerBlockedPeriodWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourtBlockedPeriod
        fields = ['starts_at', 'ends_at', 'type', 'reason']

    def validate(self, attrs):
        starts = attrs.get('starts_at', getattr(self.instance, 'starts_at', None))
        ends = attrs.get('ends_at', getattr(self.instance, 'ends_at', None))
        if starts is None or ends is None or ends <= starts:
            raise serializers.ValidationError({'ends_at': 'End time must be after start time.'})
        if starts <= timezone.now():
            raise serializers.ValidationError({'starts_at': 'Blocked periods must start in the future.'})
        return attrs


class VenueOwnerRegistrationSerializer(serializers.Serializer):
    name = serializers.CharField(source='full_name', max_length=150)
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, validators=[phone_validator])
    city = serializers.CharField(max_length=100)
    password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8)
    business_name = serializers.CharField(max_length=200)
    address = serializers.CharField(max_length=255)

from django.test import SimpleTestCase
from django.urls import resolve


class VenuePortalRouteTests(SimpleTestCase):
    def test_venue_owner_routes_are_owned_by_venue_portal(self):
        for route in (
            '/api/v1/auth/register/venue-owner/',
            '/api/v1/users/me/venue-owner-applications/',
            '/api/v1/venue-owner/dashboard/',
            '/api/v1/venue-owner/venues/',
            '/api/v1/venue-owner/bookings/',
        ):
            with self.subTest(route=route):
                match = resolve(route)
                self.assertEqual(match.func.view_class.__module__, 'my_app_venue_portal.views')


from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework.test import APITestCase
from my_app.models import Court, CourtBooking, Sport, Venue, VenueSport

User = get_user_model()


class VenueOwnerOperationsTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='venue-ops-owner', email='venue-ops@example.com', password='StrongPass!123')
        owner_group, _ = Group.objects.get_or_create(name='SportMate Venue Owners')
        self.owner.groups.add(owner_group)
        self.other_owner = User.objects.create_user(username='other-venue-owner', email='other-venue@example.com', password='StrongPass!123')
        self.other_owner.groups.add(owner_group)
        self.admin = User.objects.create_superuser(username='venue-superowner', email='venue-admin@example.com', password='StrongPass!123')
        self.sport = Sport.objects.get(name='Badminton')
        self.venue = Venue.objects.create(
            owner=self.owner, name='Owner Operations Arena', address='1 Main Road', city='Bengaluru',
            latitude=12.9716, longitude=77.5946, opening_time='06:00', closing_time='22:00', active=True,
        )
        VenueSport.objects.create(venue=self.venue, sport=self.sport)
        self.court = Court.objects.create(venue=self.venue, sport=self.sport, name='Court O1', capacity=4, price_per_hour=400)

    def test_owner_can_create_pending_venue_and_manage_own_courts(self):
        self.client.force_authenticate(self.owner)
        created = self.client.post('/api/v1/venue-owner/venues/', {
            'name': 'New Owner Ground', 'address': '12 Court Road', 'city': 'Mysuru',
            'latitude': 12.3, 'longitude': 76.6, 'opening_time': '06:00', 'closing_time': '22:00',
            'timezone': 'Asia/Kolkata', 'sport_ids': [self.sport.id],
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertFalse(Venue.objects.get(public_id=created.data['data']['public_id']).active)
        court = self.client.post(f"/api/v1/venue-owner/venues/{self.venue.public_id}/courts/", {
            'name': 'Court O2', 'sport_id': self.sport.id, 'capacity': 6, 'price_per_hour': '500.00',
        }, format='json')
        self.assertEqual(court.status_code, 201, court.data)
        deactivated = self.client.delete(f"/api/v1/venue-owner/courts/{court.data['data']['public_id']}/")
        self.assertEqual(deactivated.status_code, 200)
        self.assertFalse(Court.objects.get(public_id=court.data['data']['public_id']).active)

    def test_owner_is_scoped_and_cannot_approve_venue(self):
        self.client.force_authenticate(self.other_owner)
        self.assertEqual(self.client.get('/api/v1/venue-owner/venues/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/').status_code, 404)
        pending = Venue.objects.create(
            owner=self.other_owner, name='Pending Owner Ground', address='12 Court Road', city='Bengaluru',
            latitude=12.3, longitude=76.6, opening_time='06:00', closing_time='22:00', active=False,
        )
        response = self.client.patch(f'/api/v1/venue-owner/venues/{pending.public_id}/', {'active': True}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_superowner_can_approve_and_transfer_only_to_approved_owner(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f'/api/v1/admin/venues/{self.venue.public_id}/', {
            'active': False, 'owner_id': str(self.other_owner.public_id), 'reason': 'Operator reassignment',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.venue.refresh_from_db()
        self.assertFalse(self.venue.active)
        self.assertEqual(self.venue.owner_id, self.other_owner.id)
        pending = User.objects.create_user(username='not-approved-owner', email='not-approved@example.com', password='StrongPass!123')
        denied = self.client.patch(f'/api/v1/admin/venues/{self.venue.public_id}/', {'owner_id': str(pending.public_id)}, format='json')
        self.assertEqual(denied.status_code, 400)

    def test_owner_can_read_bookings_and_manage_future_maintenance_periods(self):
        self.client.force_authenticate(self.owner)
        now = timezone.now()
        booking = CourtBooking.objects.create(
            user=self.other_owner, court=self.court, venue=self.venue,
            booking_date=timezone.localdate() + timedelta(days=2),
            starts_at=now + timedelta(days=2), ends_at=now + timedelta(days=2, hours=1),
            status=CourtBooking.Status.CONFIRMED,
        )
        bookings = self.client.get('/api/v1/venue-owner/bookings/')
        self.assertEqual(bookings.status_code, 200)
        self.assertEqual(bookings.data['data'][0]['public_id'], str(booking.public_id))

        starts = now + timedelta(days=3)
        ends = starts + timedelta(hours=1)
        created = self.client.post(
            f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/{self.court.public_id}/blocked-periods/',
            {'starts_at': starts.isoformat(), 'ends_at': ends.isoformat(), 'type': 'maintenance', 'reason': 'Court resurfacing'},
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        listed = self.client.get(f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/{self.court.public_id}/blocked-periods/')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.data['data']), 1)
        removed = self.client.delete(f"/api/v1/venue-owner/blocked-periods/{created.data['data']['public_id']}/")
        self.assertEqual(removed.status_code, 204)

    def test_owner_reports_are_scoped_and_blocked_periods_cannot_overlap(self):
        self.client.force_authenticate(self.owner)
        now = timezone.now()
        booking = CourtBooking.objects.create(
            user=self.other_owner, court=self.court, venue=self.venue,
            booking_date=timezone.localdate(), starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=3), status=CourtBooking.Status.CONFIRMED,
        )
        report = self.client.get('/api/v1/venue-owner/reports/', {
            'from': str(booking.booking_date), 'to': str(booking.booking_date),
        })
        self.assertEqual(report.status_code, 200, report.data)
        self.assertEqual(report.data['data']['bookings_total'], 1)
        self.assertEqual(report.data['data']['booked_hours'], 1.0)

        starts = now + timedelta(days=3)
        first = self.client.post(
            f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/{self.court.public_id}/blocked-periods/',
            {'starts_at': starts.isoformat(), 'ends_at': (starts + timedelta(hours=2)).isoformat(), 'type': 'maintenance'}, format='json',
        )
        self.assertEqual(first.status_code, 201, first.data)
        overlapping = self.client.post(
            f'/api/v1/venue-owner/venues/{self.venue.public_id}/courts/{self.court.public_id}/blocked-periods/',
            {'starts_at': (starts + timedelta(hours=1)).isoformat(), 'ends_at': (starts + timedelta(hours=3)).isoformat(), 'type': 'blocked'}, format='json',
        )
        self.assertEqual(overlapping.status_code, 400)

    def test_owner_cannot_deactivate_court_with_upcoming_booking(self):
        self.client.force_authenticate(self.owner)
        now = timezone.now()
        CourtBooking.objects.create(
            user=self.other_owner, court=self.court, venue=self.venue,
            booking_date=timezone.localdate() + timedelta(days=1), starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=1), status=CourtBooking.Status.CONFIRMED,
        )
        response = self.client.delete(f'/api/v1/venue-owner/courts/{self.court.public_id}/')
        self.assertEqual(response.status_code, 400, response.data)
        patch_response = self.client.patch(f'/api/v1/venue-owner/courts/{self.court.public_id}/', {'active': False}, format='json')
        self.assertEqual(patch_response.status_code, 400, patch_response.data)
        self.court.refresh_from_db()
        self.assertTrue(self.court.active)

    def test_non_owner_cannot_read_owner_dashboard(self):
        self.client.force_authenticate(User.objects.create_user(username='regular-player', password='StrongPass!123'))
        self.assertEqual(self.client.get('/api/v1/venue-owner/dashboard/').status_code, 403)

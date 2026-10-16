from django.test import SimpleTestCase
from django.urls import resolve


class AdminPortalRouteTests(SimpleTestCase):
    def test_admin_routes_are_owned_by_admin_portal(self):
        for route in (
            '/api/v1/admin/dashboard/',
            '/api/v1/admin/venues/',
            '/api/v1/admin/venue-owner-applications/',
            '/api/v1/admin/college-result-disputes/',
            '/api/v1/admin/reports/',
            '/api/v1/reports/',
        ):
            with self.subTest(route=route):
                match = resolve(route)
                self.assertEqual(match.func.view_class.__module__, 'my_app_admin_portal.views')


from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase
from my_app_admin_portal.models import ModerationAction

User = get_user_model()


class ModerationApiTests(APITestCase):
    def setUp(self):
        self.reporter = User.objects.create_user(username='reporter', email='reporter@example.com', password='StrongPass!123')
        self.target = User.objects.create_user(username='target', email='target@example.com', password='StrongPass!123')
        self.moderator = User.objects.create_user(username='moderator', email='moderator@example.com', password='StrongPass!123')
        self.moderator.groups.add(Group.objects.create(name='SportMate Moderators'))
        self.admin = User.objects.create_user(username='platform-admin', email='platform-admin@example.com', password='StrongPass!123')
        self.admin.groups.add(Group.objects.create(name='SportMate Admins'))

    def create_report(self):
        self.client.force_authenticate(self.reporter)
        return self.client.post('/api/v1/reports/', {
            'target_type': 'user', 'target_public_id': str(self.target.public_id),
            'reason': 'abuse', 'description': 'Repeated abusive conduct.',
        }, format='json')

    def test_report_ownership_moderation_and_restore(self):
        created = self.create_report()
        self.assertEqual(created.status_code, 201)
        report_id = created.data['data']['public_id']
        self.assertEqual(self.client.get('/api/v1/admin/dashboard/').status_code, 403)
        self.client.force_authenticate(self.moderator)
        suspended = self.client.patch(f'/api/v1/reports/{report_id}/', {'action': 'suspend', 'action_reason': 'Policy violation.'}, format='json')
        self.assertEqual(suspended.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_status, User.AccountStatus.SUSPENDED)
        self.assertEqual(ModerationAction.objects.filter(action='suspend').count(), 1)
        restored = self.client.patch(f'/api/v1/reports/{report_id}/', {'action': 'restore', 'status': 'resolved', 'resolution': 'Restriction lifted.'}, format='json')
        self.assertEqual(restored.status_code, 200)
        self.target.refresh_from_db()
        self.assertEqual(self.target.account_status, User.AccountStatus.ACTIVE)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get('/api/v1/admin/dashboard/').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/admin/moderation/actions/').status_code, 200)

    def test_normal_user_cannot_access_another_report(self):
        created = self.create_report()
        report_id = created.data['data']['public_id']
        self.client.force_authenticate(self.target)
        self.assertEqual(self.client.get(f'/api/v1/reports/{report_id}/').status_code, 404)

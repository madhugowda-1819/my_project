from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Sport

User = get_user_model()


class BasicTests(TestCase):
    def test_create_user(self):
        u = User.objects.create_user(username='john', email='j@x.com',
                                     password='pw123456')
        self.assertEqual(u.email, 'j@x.com')

    def test_create_sport(self):
        s = Sport.objects.create(name='Cricket', icon='🏏')
        self.assertEqual(str(s), 'Cricket')

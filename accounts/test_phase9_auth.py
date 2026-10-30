from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserProfile


@override_settings(SECURE_SSL_REDIRECT=False)
class SignupPasswordValidatorTests(TestCase):
    """Regression: signup applies the configured AUTH_PASSWORD_VALIDATORS."""

    def payload(self, password, username='newbaker'):
        return {'first_name': 'New', 'last_name': 'Baker', 'username': username,
                'email': f'{username}@example.test', 'mobile_number': '9876543210', 'password': password}

    def test_common_password_rejected(self):
        r = APIClient().post('/api/accounts/signup/', self.payload('password123'), format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('password', r.json())
        self.assertFalse(User.objects.filter(username='newbaker').exists())

    def test_numeric_password_rejected(self):
        r = APIClient().post('/api/accounts/signup/', self.payload('83920174655'), format='json')
        self.assertEqual(r.status_code, 400)

    def test_password_similar_to_username_rejected(self):
        r = APIClient().post('/api/accounts/signup/', self.payload('newbaker1', username='newbaker1'), format='json')
        self.assertEqual(r.status_code, 400)

    def test_strong_password_accepted(self):
        r = APIClient().post('/api/accounts/signup/', self.payload('Velvet-Sponge-4821'), format='json')
        self.assertIn(r.status_code, (200, 201), r.content)


@override_settings(SECURE_SSL_REDIRECT=False)
class UnverifiedSigninMessageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('pending1', 'pending1@example.test', 'Velvet-Sponge-4821', is_active=False)
        UserProfile.objects.create(user=self.user, mobile_number='9876543211', is_verified=False)

    def signin(self, password):
        return APIClient().post('/api/accounts/signin/', {'username': 'pending1', 'password': password}, format='json')

    def test_correct_password_gets_verification_message(self):
        r = self.signin('Velvet-Sponge-4821')
        self.assertEqual(r.status_code, 400)
        self.assertIn('not been verified', r.content.decode())
        self.assertNotIn('token', r.json())

    def test_wrong_password_gets_generic_message(self):
        r = self.signin('wrong-password-1')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Invalid username or password', r.content.decode())
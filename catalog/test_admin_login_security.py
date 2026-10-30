from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient


@override_settings(SECURE_SSL_REDIRECT=False)
class AdminLoginSecurityTests(TestCase):
    """Regression: admin login by email must verify the password."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user('staffer', 'staff@example.test', 'Right-Pass-123', is_staff=True)
        User.objects.create_user('shopper', 'shopper@example.test', 'Right-Pass-123')

    def login(self, username, password):
        return self.client.post('/api/admin/login/', {'username': username, 'password': password}, format='json')

    def test_email_with_wrong_password_is_rejected(self):
        r = self.login('staff@example.test', 'wrong-password')
        self.assertEqual(r.status_code, 401)
        self.assertNotIn('token', r.json())

    def test_email_with_correct_password_succeeds(self):
        r = self.login('STAFF@example.test', 'Right-Pass-123')
        self.assertEqual(r.status_code, 200)
        self.assertIn('token', r.json())

    def test_username_with_wrong_password_is_rejected(self):
        self.assertEqual(self.login('staffer', 'wrong-password').status_code, 401)

    def test_inactive_staff_is_rejected(self):
        self.staff.is_active = False
        self.staff.save()
        self.assertEqual(self.login('staff@example.test', 'Right-Pass-123').status_code, 401)
        self.assertEqual(self.login('staffer', 'Right-Pass-123').status_code, 401)

    def test_non_staff_is_rejected(self):
        self.assertEqual(self.login('shopper@example.test', 'Right-Pass-123').status_code, 401)
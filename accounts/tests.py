from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import PasswordResetToken


class PasswordResetFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='resetuser',
            email='resetuser@example.com',
            password='OldPassword123',
            first_name='Reset',
            last_name='User',
            is_active=True,
        )
        self.user.profile = self.user.profile if hasattr(self.user, 'profile') else None
        if not hasattr(self.user, 'profile'):
            from .models import UserProfile
            self.user.profile = UserProfile.objects.create(
                user=self.user,
                mobile_number='9876543210',
                is_verified=True,
                email_verified=True,
                mobile_verified=True,
            )
        self.user.profile.is_verified = True
        self.user.profile.email_verified = True
        self.user.profile.mobile_verified = True
        self.user.profile.save(update_fields=['is_verified', 'email_verified', 'mobile_verified'])

    def test_request_login_otp_for_verified_mobile_generates_hash_and_returns_generic_message(self):
        response = self.client.post('/api/accounts/request-login-otp/', {'mobile': '+91 9876543210'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('message', data)
        self.assertIn('otp', data['message'].lower())
        self.user.profile.refresh_from_db()
        self.assertIsNotNone(self.user.profile.otp_hash)
        self.assertIsNotNone(self.user.profile.otp_expires_at)

    def test_verify_login_otp_returns_auth_token_for_verified_user(self):
        self.user.profile.otp_hash = make_password('123456')
        self.user.profile.otp_expires_at = timezone.now() + timedelta(minutes=5)
        self.user.profile.otp_attempts = 0
        self.user.profile.mobile_verified = True
        self.user.profile.is_verified = True
        self.user.profile.save(update_fields=['otp_hash', 'otp_expires_at', 'otp_attempts', 'mobile_verified', 'is_verified'])

        response = self.client.post('/api/accounts/verify-login-otp/', {'mobile': '9876543210', 'otp': '123456'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('token', data)
        self.assertEqual(data['user']['username'], self.user.username)

    def test_verify_login_otp_rejects_wrong_code(self):
        self.user.profile.otp_hash = make_password('654321')
        self.user.profile.otp_expires_at = timezone.now() + timedelta(minutes=5)
        self.user.profile.otp_attempts = 0
        self.user.profile.mobile_verified = True
        self.user.profile.is_verified = True
        self.user.profile.save(update_fields=['otp_hash', 'otp_expires_at', 'otp_attempts', 'mobile_verified', 'is_verified'])

        response = self.client.post('/api/accounts/verify-login-otp/', {'mobile': '9876543210', 'otp': '123456'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.otp_attempts, 1)

    def test_forgot_password_returns_generic_response_and_sends_email(self):
        response = self.client.post('/api/accounts/forgot-password/', {'email': 'resetuser@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('message', data)
        self.assertIn('password reset link', data['message'].lower())
        self.assertTrue(PasswordResetToken.objects.filter(user=self.user).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Reset Your PinkBakes Password', mail.outbox[0].subject)

    def test_reset_password_flow_updates_password_and_invalidates_token(self):
        reset_token = PasswordResetToken.objects.create(
            user=self.user,
            token_hash=PasswordResetToken.hash_token('secure-token-123'),
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post('/api/accounts/reset-password/', {
            'token': 'secure-token-123',
            'password': 'NewPassword123',
            'confirm_password': 'NewPassword123',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPassword123'))
        reset_token.refresh_from_db()
        self.assertIsNotNone(reset_token.used_at)

    def test_verify_reset_token_rejects_expired_token(self):
        expired_token = PasswordResetToken.objects.create(
            user=self.user,
            token_hash=PasswordResetToken.hash_token('expired-token'),
            expires_at=timezone.now() - timedelta(hours=1),
        )

        response = self.client.post('/api/accounts/verify-reset-token/', {'token': 'expired-token'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        expired_token.refresh_from_db()
        self.assertIsNone(expired_token.used_at)

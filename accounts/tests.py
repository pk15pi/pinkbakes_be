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



class SigninAndMeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="signinuser",
            email="signinuser@example.com",
            password="SecurePass123",
            first_name="Sign",
            last_name="In",
            is_active=True,
        )
        from .models import UserProfile
        UserProfile.objects.create(
            user=self.user,
            mobile_number="9876511111",
            is_verified=True,
            email_verified=True,
            mobile_verified=True,
        )

    def test_signin_returns_token_for_verified_user(self):
        response = self.client.post(
            "/api/accounts/signin/",
            {"username": "signinuser", "password": "SecurePass123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("token", data)
        self.assertEqual(data["user"]["username"], "signinuser")

    def test_signin_rejects_bad_password(self):
        response = self.client.post(
            "/api/accounts/signin/",
            {"username": "signinuser", "password": "WrongPass999"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_signin_rejects_unverified_user(self):
        unverified = User.objects.create_user(
            username="unverified",
            email="unverified@example.com",
            password="SecurePass123",
            is_active=False,
        )
        from .models import UserProfile
        UserProfile.objects.create(
            user=unverified,
            mobile_number="9876522222",
            is_verified=False,
        )
        response = self.client.post(
            "/api/accounts/signin/",
            {"username": "unverified", "password": "SecurePass123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_me_requires_auth_and_returns_profile(self):
        denied = self.client.get("/api/accounts/me/")
        self.assertEqual(denied.status_code, status.HTTP_401_UNAUTHORIZED)

        from rest_framework.authtoken.models import Token
        token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        ok = self.client.get("/api/accounts/me/")
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        data = ok.json()
        self.assertEqual(data["username"], "signinuser")
        self.assertTrue(data["is_verified"])
        self.assertEqual(data["mobile_number"], "9876511111")


class OtpLockoutAndThrottleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="otplock",
            email="otplock@example.com",
            password="SecurePass123",
            is_active=True,
        )
        from .models import UserProfile
        self.profile = UserProfile.objects.create(
            user=self.user,
            mobile_number="9876533333",
            is_verified=True,
            email_verified=True,
            mobile_verified=True,
        )

    def test_login_otp_lockout_after_five_failures(self):
        self.profile.otp_hash = make_password("111111")
        self.profile.otp_expires_at = timezone.now() + timedelta(minutes=5)
        self.profile.otp_attempts = 0
        self.profile.save(update_fields=["otp_hash", "otp_expires_at", "otp_attempts"])

        for _ in range(5):
            response = self.client.post(
                "/api/accounts/verify-login-otp/",
                {"mobile": "9876533333", "otp": "000000"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        locked = self.client.post(
            "/api/accounts/verify-login-otp/",
            {"mobile": "9876533333", "otp": "111111"},
            format="json",
        )
        self.assertEqual(locked.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.profile.refresh_from_db()
        self.assertGreaterEqual(self.profile.otp_attempts, 5)

    def test_verify_otp_lockout_after_five_failures(self):
        self.profile.otp_hash = make_password("222222")
        self.profile.otp_expires_at = timezone.now() + timedelta(minutes=5)
        self.profile.otp_attempts = 0
        self.profile.save(update_fields=["otp_hash", "otp_expires_at", "otp_attempts"])

        for _ in range(5):
            response = self.client.post(
                "/api/accounts/verify-otp/",
                {"email": "otplock@example.com", "otp": "000000"},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        locked = self.client.post(
            "/api/accounts/verify-otp/",
            {"email": "otplock@example.com", "otp": "222222"},
            format="json",
        )
        self.assertEqual(locked.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_send_verification_throttles_within_one_minute(self):
        self.profile.otp_last_sent_at = timezone.now()
        self.profile.save(update_fields=["otp_last_sent_at"])
        response = self.client.post(
            "/api/accounts/send-verification/",
            {"email": "otplock@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

"""Security-focused regression tests for PinkBakes API hardening."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import CustomerAddress, UserProfile
from catalog.models import Order, Payment, Product
from catalog.views import PaymentService


class AuthSecretLeakTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_signup_does_not_return_otp_or_verification_secrets(self):
        response = self.client.post(
            "/api/accounts/signup/",
            {
                "first_name": "Sec",
                "last_name": "User",
                "username": "secuser1",
                "email": "secuser1@example.com",
                "mobile_number": "9876500001",
                "password": "SecurePass123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertNotIn("otp", data)
        self.assertNotIn("verification_token", data)
        self.assertNotIn("verification_link", data)
        self.assertNotIn("token", data)
        profile = UserProfile.objects.get(user__username="secuser1")
        self.assertIsNone(profile.otp_code)
        self.assertIsNotNone(profile.otp_hash)
        self.assertTrue(len(mail.outbox) >= 1)

    def test_request_login_otp_does_not_echo_code(self):
        user = User.objects.create_user(
            username="otpleak",
            email="otpleak@example.com",
            password="SecurePass123",
            is_active=True,
        )
        UserProfile.objects.create(
            user=user,
            mobile_number="9876500002",
            is_verified=True,
            email_verified=True,
            mobile_verified=True,
        )
        response = self.client.post(
            "/api/accounts/request-login-otp/",
            {"mobile": "9876500002"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertNotIn("otp", data)
        self.assertIn("otp", data["message"].lower())
        user.profile.refresh_from_db()
        self.assertIsNone(user.profile.otp_code)
        self.assertIsNotNone(user.profile.otp_hash)

    def test_send_verification_is_generic_for_unknown_email(self):
        response = self.client.post(
            "/api/accounts/send-verification/",
            {"email": "nobody@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertNotIn("otp", data)
        self.assertNotIn("verification_token", data)


class AuthorizationIdorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="owner", email="owner@example.com", password="Pass12345!", is_active=True)
        UserProfile.objects.create(user=self.owner, mobile_number="9000000001", is_verified=True)
        self.other = User.objects.create_user(username="other", email="other@example.com", password="Pass12345!", is_active=True)
        UserProfile.objects.create(user=self.other, mobile_number="9000000002", is_verified=True)
        self.product = Product.objects.create(
            name="Security Cake",
            category="Cakes",
            price=Decimal("500.00"),
            is_active=True,
            status="published",
            available_quantity=10,
        )
        self.order = Order.objects.create(
            user=self.owner,
            order_number="PB-SEC-1",
            status="CONFIRMED",
            payment_status="paid",
            customer_name="Owner",
            customer_email="owner@example.com",
            customer_mobile="9000000001",
            shipping_address="1 Test St",
            city="Mumbai",
            state="MH",
            postal_code="400001",
            country="India",
            subtotal_amount=Decimal("500.00"),
            discount_amount=Decimal("0"),
            delivery_fee=Decimal("0"),
            tax_amount=Decimal("0"),
            total_amount=Decimal("500.00"),
        )
        self.payment = Payment.objects.create(
            order=self.order,
            user=self.owner,
            gateway="razorpay",
            gateway_order_id="order_sec_1",
            amount=Decimal("500.00"),
            currency="INR",
            status="paid",
        )
        self.address = CustomerAddress.objects.create(
            user=self.owner,
            full_name="Owner",
            mobile_number="9000000001",
            address_line_1="1 Test St",
            city="Mumbai",
            state="MH",
            postal_code="400001",
        )

    def test_other_user_cannot_view_order(self):
        token = Token.objects.create(user=self.other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get(f"/api/orders/{self.order.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_user_cannot_view_payment(self):
        token = Token.objects.create(user=self.other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get(f"/api/payments/{self.payment.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_customer_cannot_access_admin_dashboard(self):
        token = Token.objects.create(user=self.other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get("/api/admin/dashboard/")
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_401_UNAUTHORIZED))

    def test_customer_cannot_spoof_delivery_location(self):
        token = Token.objects.create(user=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.post(
            f"/api/orders/{self.order.id}/location/",
            {"latitude": 19.1, "longitude": 72.8},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_address_idor_forbidden(self):
        token = Token.objects.create(user=self.other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get(f"/api/addresses/{self.address.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PaymentSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="payuser", email="payuser@example.com", password="Pass12345!", is_active=True)
        UserProfile.objects.create(user=self.user, mobile_number="9000000003", is_verified=True)
        self.product = Product.objects.create(
            name="Pay Cake",
            category="Cakes",
            price=Decimal("400.00"),
            is_active=True,
            status="published",
            available_quantity=5,
        )
        self.token = Token.objects.create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_invalid_razorpay_signature_rejected(self):
        order = Order.objects.create(
            user=self.user,
            order_number="PB-PAY-1",
            status="PENDING",
            payment_status="pending",
            customer_name="Pay",
            customer_email="payuser@example.com",
            customer_mobile="9000000003",
            shipping_address="Addr",
            city="Mumbai",
            state="MH",
            postal_code="400001",
            country="India",
            subtotal_amount=Decimal("400.00"),
            total_amount=Decimal("400.00"),
        )
        payment = Payment.objects.create(
            order=order,
            user=self.user,
            gateway="razorpay",
            gateway_order_id="order_pay_bad_sig",
            amount=Decimal("400.00"),
            currency="INR",
            status="created",
        )
        response = self.client.post(
            "/api/payments/verify/",
            {
                "razorpay_order_id": payment.gateway_order_id,
                "razorpay_payment_id": "pay_fake",
                "razorpay_signature": "not-a-valid-signature",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        payment.refresh_from_db()
        self.assertNotEqual(payment.status, "paid")

    def test_payment_amount_mismatch_rejected(self):
        order = Order.objects.create(
            user=self.user,
            order_number="PB-PAY-2",
            status="PENDING",
            payment_status="pending",
            customer_name="Pay",
            customer_email="payuser@example.com",
            customer_mobile="9000000003",
            shipping_address="Addr",
            city="Mumbai",
            state="MH",
            postal_code="400001",
            country="India",
            subtotal_amount=Decimal("400.00"),
            total_amount=Decimal("400.00"),
        )
        payment = Payment.objects.create(
            order=order,
            user=self.user,
            gateway="razorpay",
            gateway_order_id="order_pay_amt",
            amount=Decimal("400.00"),
            currency="INR",
            status="created",
        )
        good_sig = PaymentService.build_signature(
            payment.gateway_order_id, "pay_ok", "test_razorpay_secret"
        )
        response = self.client.post(
            "/api/payments/verify/",
            {
                "razorpay_order_id": payment.gateway_order_id,
                "razorpay_payment_id": "pay_ok",
                "razorpay_signature": good_sig,
                "amount": 1,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_client_cannot_set_order_total_via_payment_create(self):
        response = self.client.post(
            "/api/payments/create/",
            {
                "items": [{"id": self.product.id, "quantity": 1}],
                "total_amount": "1.00",
                "customer_name": "Pay",
                "customer_email": "payuser@example.com",
                "customer_mobile": "9000000003",
                "shipping_address": "Addr",
                "city": "Mumbai",
                "state": "MH",
                "postal_code": "400001",
                "country": "India",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # Backend is source of truth: amount is product price in paise, not client total.
        self.assertEqual(data["amount"], 40000)


class UploadAndLogoutSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="secadmin",
            email="secadmin@example.com",
            password="Pass12345!",
            is_active=True,
            is_staff=True,
        )
        self.product = Product.objects.create(
            name="Upload Cake",
            category="Cakes",
            price=Decimal("100.00"),
            is_active=True,
            status="published",
            available_quantity=1,
        )
        self.token = Token.objects.create(user=self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_upload_rejects_javascript_url(self):
        response = self.client.post(
            f"/api/catalog/products/{self.product.id}/image/",
            {"image": "javascript:alert(1)"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_logout_invalidates_token(self):
        response = self.client.post("/api/catalog/admin/logout/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(user=self.admin).exists())

    def test_password_reset_invalidates_tokens(self):
        user = User.objects.create_user(
            username="resetsec",
            email="resetsec@example.com",
            password="OldPassword123",
            is_active=True,
        )
        UserProfile.objects.create(user=user, mobile_number="9000000099", is_verified=True)
        Token.objects.create(user=user)
        from accounts.models import PasswordResetToken

        raw = "reset-token-security"
        PasswordResetToken.objects.create(
            user=user,
            token_hash=PasswordResetToken.hash_token(raw),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        response = self.client.post(
            "/api/accounts/reset-password/",
            {
                "token": raw,
                "password": "NewPassword123",
                "confirm_password": "NewPassword123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(user=user).exists())



class TrackingAndNotificationIdorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username="trackowner", email="trackowner@example.com", password="Pass12345!", is_active=True
        )
        UserProfile.objects.create(user=self.owner, mobile_number="9000000101", is_verified=True)
        self.other = User.objects.create_user(
            username="trackother", email="trackother@example.com", password="Pass12345!", is_active=True
        )
        UserProfile.objects.create(user=self.other, mobile_number="9000000102", is_verified=True)
        self.product = Product.objects.create(
            name="Track Cake",
            category="Cakes",
            price=Decimal("300.00"),
            is_active=True,
            status="published",
            available_quantity=10,
        )
        self.order = Order.objects.create(
            user=self.owner,
            order_number="PB-TRACK-001",
            customer_name="Owner",
            customer_email="trackowner@example.com",
            customer_mobile="9000000101",
            shipping_address="1 Cake Lane",
            city="Kolkata",
            state="WB",
            postal_code="700001",
            country="India",
            subtotal_amount=Decimal("300.00"),
            total_amount=Decimal("300.00"),
            status="OUT_FOR_DELIVERY",
            payment_status="paid",
        )

    def test_other_user_cannot_view_order_tracking(self):
        token = Token.objects.create(user=self.other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get(f"/api/orders/{self.order.id}/tracking/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_view_order_tracking(self):
        token = Token.objects.create(user=self.owner)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get(f"/api/orders/{self.order.id}/tracking/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["order_number"], "PB-TRACK-001")

    def test_other_user_cannot_mark_foreign_notification_read(self):
        from notifications.models import InAppNotification
        from notifications.defaults import seed_channel_configs
        seed_channel_configs()
        note = InAppNotification.objects.create(
            user=self.owner,
            title="Order update",
            body="Your cake is on the way",
            event="ORDER_STATUS_CHANGED",
        )
        token = Token.objects.create(user=self.other)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.post(f"/api/accounts/notifications/{note.id}/read/")
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from notifications import events as E
from notifications.defaults import seed_channel_configs, seed_email_templates
from notifications.models import (
    InAppNotification,
    NotificationChannelConfig,
    NotificationLog,
    NotificationPreference,
)
from notifications.service import notify, notify_order_confirmed


class NotificationServiceTests(TestCase):
    def setUp(self):
        seed_channel_configs()
        seed_email_templates()
        self.user = User.objects.create_user(
            username='notifyuser',
            email='notify@example.com',
            password='Pass12345!',
            first_name='Nia',
        )
        from accounts.models import UserProfile
        UserProfile.objects.get_or_create(
            user=self.user,
            defaults={
                'mobile_number': '9999999999',
                'is_verified': True,
                'email_verified': True,
                'mobile_verified': True,
            },
        )

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_order_confirmed_sends_email(self):
        from catalog.models import Order, OrderItem, Product

        product = Product.objects.create(
            name='Chocolate Cake', price=Decimal('500.00'), category='Birthday',
        )
        order = Order.objects.create(
            user=self.user,
            order_number='PB-TEST-001',
            customer_name='Nia',
            customer_email='notify@example.com',
            customer_mobile='9999999999',
            shipping_address='12 Sweet St',
            city='Kolkata',
            state='WB',
            postal_code='700001',
            country='India',
            subtotal_amount=Decimal('500.00'),
            total_amount=Decimal('500.00'),
            payment_status='paid',
            status='ORDER_CONFIRMED',
        )
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name,
            unit_price=Decimal('500.00'), quantity=1, subtotal=Decimal('500.00'),
        )
        mail.outbox.clear()
        results = notify_order_confirmed(order)
        self.assertTrue(any(r.get('status') == E.STATUS_SENT and r.get('channel') == 'email' for r in results))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('PB-TEST-001', mail.outbox[0].subject)
        body = mail.outbox[0].alternatives[0][0]
        self.assertIn('Nia', body)
        self.assertIn('Chocolate Cake', body)
        self.assertIn('700001', body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_preference_skips_promotional_but_not_password_reset(self):
        prefs, _ = NotificationPreference.objects.get_or_create(user=self.user)
        prefs.email_promotional = False
        prefs.email_order_updates = False
        prefs.save()

        # Ensure a promotional-like / non-critical order cancel respects opt-out for email
        NotificationChannelConfig.objects.update_or_create(
            event=E.ORDER_CANCELLED, channel='email', defaults={'is_enabled': True},
        )
        mail.outbox.clear()
        from catalog.models import Order
        order = Order.objects.create(
            user=self.user,
            order_number='PB-TEST-002',
            customer_name='Nia',
            customer_email='notify@example.com',
            customer_mobile='9999999999',
            shipping_address='12 Sweet St',
            city='Kolkata', state='WB', postal_code='700001', country='India',
            total_amount=Decimal('100.00'),
            payment_status='pending',
            status='CANCELLED',
        )
        results = notify(
            E.ORDER_CANCELLED,
            user=self.user,
            email=self.user.email,
            context={'order': order, 'reason': 'changed mind'},
            channels=['email'],
        )
        self.assertTrue(any(r.get('status') == E.STATUS_SKIPPED for r in results))
        self.assertEqual(len(mail.outbox), 0)

        # Password reset is critical — must send even if promotional off
        mail.outbox.clear()
        results = notify(
            E.PASSWORD_RESET_REQUESTED,
            user=self.user,
            email=self.user.email,
            context={'user_name': 'Nia', 'reset_url': 'https://pinkbakes.com/reset-password/abc'},
            channels=['email'],
        )
        self.assertTrue(any(r.get('status') == E.STATUS_SENT for r in results))
        self.assertEqual(len(mail.outbox), 1)

    def test_sms_skipped_when_unconfigured(self):
        results = notify(
            E.LOGIN_OTP_REQUESTED,
            user=self.user,
            phone='9999999999',
            context={'message': 'Your login code is ready'},
            channels=['sms'],
            force=True,
        )
        self.assertTrue(any(r.get('status') == E.STATUS_SKIPPED for r in results))
        log = NotificationLog.objects.filter(event=E.LOGIN_OTP_REQUESTED, channel='sms').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, E.STATUS_SKIPPED)
        self.assertNotIn('123456', log.last_error)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_idempotency_prevents_duplicate_confirmation_email(self):
        from catalog.models import Order
        order = Order.objects.create(
            user=self.user,
            order_number='PB-TEST-003',
            customer_name='Nia',
            customer_email='notify@example.com',
            customer_mobile='9999999999',
            shipping_address='12 Sweet St',
            city='Kolkata', state='WB', postal_code='700001', country='India',
            total_amount=Decimal('200.00'),
            payment_status='paid',
            status='ORDER_CONFIRMED',
        )
        mail.outbox.clear()
        notify_order_confirmed(order)
        notify_order_confirmed(order)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_retry_logs_failure(self):
        with mock.patch('notifications.channels.email.send_html_email', side_effect=RuntimeError('smtp down')):
            results = notify(
                E.PASSWORD_CHANGED,
                user=self.user,
                email=self.user.email,
                context={'user_name': 'Nia'},
                channels=['email'],
                force=True,
                idempotency_key='pwd-changed-test-1',
            )
        self.assertTrue(any(r.get('status') == E.STATUS_FAILED for r in results))
        log = NotificationLog.objects.filter(idempotency_key__startswith='pwd-changed-test-1', channel='email').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, E.STATUS_FAILED)
        self.assertGreaterEqual(log.retry_count, 1)
        self.assertIn('smtp down', log.last_error)

    def test_in_app_notification_and_mark_read_authz(self):
        other = User.objects.create_user(username='other', email='other@example.com', password='Pass12345!')
        notify(
            E.ORDER_CREATED,
            user=self.user,
            context={'message': 'Order placed', 'title': 'Order created'},
            channels=['in_app'],
            force=True,
        )
        note = InAppNotification.objects.filter(user=self.user).first()
        self.assertIsNotNone(note)

        client = APIClient()
        token = Token.objects.create(user=self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        resp = client.get('/api/accounts/notifications/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data['count'], 1)

        # Other user cannot mark this notification read
        other_token = Token.objects.create(user=other)
        client.credentials(HTTP_AUTHORIZATION=f'Token {other_token.key}')
        resp = client.post(f'/api/accounts/notifications/{note.id}/read/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        resp = client.post(f'/api/accounts/notifications/{note.id}/read/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        note.refresh_from_db()
        self.assertTrue(note.is_read)

        resp = client.post('/api/accounts/notifications/mark-all-read/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_preferences_api(self):
        client = APIClient()
        token = Token.objects.create(user=self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        resp = client.get('/api/accounts/notification-preferences/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['email_order_updates'])
        resp = client.patch('/api/accounts/notification-preferences/', {'email_promotional': False}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['email_promotional'])

    def test_admin_logs_requires_staff(self):
        client = APIClient()
        token = Token.objects.create(user=self.user)
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        resp = client.get('/api/admin/notification-logs/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        resp = client.get('/api/admin/notification-logs/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

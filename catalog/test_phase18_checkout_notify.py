"""Phase 18: the website checkout (/api/payments/create/ + /api/payments/verify/) sends notifications."""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Product
from .tests import build_razorpay_signature

PAYLOAD = {
    'customer_name': 'Web Buyer', 'customer_email': 'web@example.test', 'customer_mobile': '9876543210',
    'shipping_address': '1 Test Lane', 'city': 'Mumbai', 'state': 'Maharashtra',
    'postal_code': '400001', 'country': 'India',
}


class WebsiteCheckoutNotificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='webbuyer', email='web@example.test', password='unused-Pass-123')
        self.client.force_authenticate(self.user)
        self.product = Product.objects.create(
            name='Notify Cake', category='Cakes', price=Decimal('500.00'), status='published',
            availability='in_stock', is_active=True, available_quantity=10,
        )

    @mock.patch('catalog.views.notify_staff_new_order')
    @mock.patch('catalog.views.notify_order_confirmed')
    @mock.patch('catalog.views.notify_order_placed')
    def test_order_received_on_create_and_staff_alert_on_payment(self, placed, confirmed, staff):
        created = self.client.post('/api/payments/create/', {**PAYLOAD, 'items': [{'id': self.product.id, 'quantity': 1}]}, format='json')
        self.assertEqual(created.status_code, 200, created.content)
        placed.assert_called_once()
        self.assertEqual(placed.call_args[0][0].id, created.json()['order_id'])
        staff.assert_not_called()  # unpaid order: no staff alert yet

        order_id = created.json()['payment_order_id']
        signature = build_razorpay_signature(order_id, 'pay_phase18', 'test_razorpay_secret')
        verified = self.client.post('/api/payments/verify/', {
            'razorpay_order_id': order_id, 'razorpay_payment_id': 'pay_phase18', 'razorpay_signature': signature,
        }, format='json')
        self.assertEqual(verified.status_code, 200, verified.content)
        staff.assert_called_once()
        confirmed.assert_called_once()
        placed.assert_called_once()  # not re-sent on payment

    @mock.patch('catalog.views.notify_order_placed', side_effect=RuntimeError('mail down'))
    def test_notification_failure_does_not_break_checkout(self, placed):
        created = self.client.post('/api/payments/create/', {**PAYLOAD, 'items': [{'id': self.product.id, 'quantity': 1}]}, format='json')
        self.assertEqual(created.status_code, 200, created.content)
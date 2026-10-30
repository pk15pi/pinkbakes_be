"""Whole-rupee rounding: displayed price == charged price."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import Coupon, DeliveryZone, Order, OrderItem, Product
from .pricing import discounted_unit_price, round_rupees


class RupeeRoundingHelperTests(TestCase):
    def test_round_half_up(self):
        self.assertEqual(round_rupees(Decimal('629.10')), Decimal('629.00'))
        self.assertEqual(round_rupees(Decimal('760.50')), Decimal('761.00'))
        self.assertEqual(round_rupees(Decimal('1376.15')), Decimal('1376.00'))
        self.assertEqual(round_rupees(49.5), Decimal('50.00'))
        self.assertEqual(round_rupees(None), Decimal('0.00'))

    def test_discounted_unit_price(self):
        self.assertEqual(discounted_unit_price(Decimal('1500'), 10), Decimal('1350.00'))
        self.assertEqual(discounted_unit_price(Decimal('699'), 10), Decimal('629.00'))
        self.assertEqual(discounted_unit_price(Decimal('1619'), 15), Decimal('1376.00'))
        self.assertEqual(discounted_unit_price(Decimal('845'), 10), Decimal('761.00'))
        self.assertEqual(discounted_unit_price(Decimal('699'), 0), Decimal('699.00'))


class RupeeRoundingFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='rounder', email='rounder@example.com', password='Pass12345!')
        common = dict(
            category='Birthday Cakes', is_active=True, status='published', availability='in_stock',
            available_quantity=50, reserved_quantity=0, sold_quantity=0, low_stock_threshold=2,
        )
        self.berry = Product.objects.create(name='Berry Round', price=Decimal('699.00'), discount=10, **common)
        self.sized = Product.objects.create(
            name='Sized Round', price=Decimal('899.00'), discount=15,
            size_prices={'0.5 kg': '899.00', '1 kg': '1619.00'}, **common,
        )

    def _checkout(self, items, **extra):
        self.client.force_authenticate(user=self.user)
        data = {
            'items': items, 'customer_name': 'Rounder', 'customer_email': 'rounder@example.com',
            'customer_mobile': '9876543210', 'shipping_address': '1 Test Street', 'city': 'Mumbai',
            'state': 'Maharashtra', 'postal_code': '400001', 'country': 'India',
        }
        data.update(extra)
        return self.client.post('/api/orders/checkout/', data, format='json')

    def test_model_and_api_discounted_price_are_whole(self):
        self.assertEqual(self.berry.discounted_price, Decimal('629.00'))
        self.assertEqual(self.sized.discounted_price_for_size('1 kg'), Decimal('1376.00'))
        self.assertEqual(self.sized.discounted_price_for_size('0.5 kg'), Decimal('764.00'))
        listing = self.client.get('/api/catalog/products/')
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        body = listing.json()
        rows = body['results'] if isinstance(body, dict) else body
        row = next(r for r in rows if r['id'] == self.berry.id)
        self.assertEqual(float(row['discounted_price']), 629.0)

    def test_checkout_total_is_sum_of_rounded_lines(self):
        response = self._checkout([
            {'id': self.berry.id, 'quantity': 3},
            {'id': self.sized.id, 'quantity': 1, 'options': {'size': '1 kg'}},
        ])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        items = list(OrderItem.objects.filter(order_id=response.json()['id']).order_by('id'))
        self.assertEqual([i.unit_price for i in items], [Decimal('629.00'), Decimal('1376.00')])
        order = Order.objects.get(id=response.json()['id'])
        self.assertEqual(order.subtotal_amount, Decimal('1887.00') + Decimal('1376.00'))
        self.assertEqual(order.subtotal_amount, sum(i.subtotal for i in items))
        self.assertEqual(order.total_amount % 1, 0)

    def test_coupon_on_rounded_price_is_whole(self):
        Coupon.objects.create(
            code='ROUND7', name='Round 7', discount_type='percentage', discount_value=Decimal('7'),
            minimum_order_amount=Decimal('0'), is_active=True, applies_to='all',
        )
        items = [{'id': self.berry.id, 'quantity': 1}]
        self.client.force_authenticate(user=self.user)
        preview = self.client.post('/api/coupons/validate/', {'code': 'ROUND7', 'items': items}, format='json')
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.content)
        # 7% of 629 = 44.03 -> 44
        self.assertEqual(float(preview.json()['discount_amount']), 44.0)
        response = self._checkout(items, coupon_code='ROUND7')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        order = Order.objects.get(id=response.json()['id'])
        self.assertEqual(order.subtotal_amount, Decimal('629.00'))
        self.assertEqual(order.coupon_discount_amount, Decimal('44.00'))
        self.assertEqual(order.total_amount % 1, 0)


class DeliveryChargeRoundingTests(TestCase):
    def test_zone_charge_rounded(self):
        from .delivery import compute_delivery
        DeliveryZone.objects.create(name='Round Zone', postal_codes=['560001'], delivery_charge=Decimal('49.50'), is_active=True)
        result = compute_delivery(Decimal('100.00'), '560001')
        self.assertTrue(result['eligible'], result)
        self.assertEqual(result['charge'], Decimal('50.00'))

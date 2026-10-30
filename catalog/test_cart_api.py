from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .cart import CART_QTY_MAX
from .models import CartItem, Order, Payment, Product
from .views import PaymentService

URL = '/api/cart/'
MERGE = '/api/cart/merge/'


class CartApiTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user(username='cart_a', email='a@example.com', password='Pass-word-123!')
        self.b = User.objects.create_user(username='cart_b', email='b@example.com', password='Pass-word-123!')
        common = dict(category='Birthday Cakes', is_active=True, status='published', availability='in_stock', available_quantity=50)
        self.cake = Product.objects.create(name='Cart Plain Cake', price=Decimal('500.00'), **common)
        self.sized = Product.objects.create(name='Cart Sized Cake', price=Decimal('700.00'),
                                            size_prices={'1 kg': '800.00', '2 kg': '1500.00'}, **common)
        self.client = APIClient()

    def as_user(self, user):
        self.client.force_authenticate(user=user)
        return self.client

    def put(self, user, items):
        return self.as_user(user).put(URL, {'items': items}, format='json')

    def test_auth_required(self):
        anon = APIClient()
        self.assertIn(anon.get(URL).status_code, (401, 403))
        self.assertIn(anon.put(URL, {'items': []}, format='json').status_code, (401, 403))
        self.assertIn(anon.post(MERGE, {'items': []}, format='json').status_code, (401, 403))

    def test_put_then_get_reprices_and_ignores_client_prices(self):
        r = self.put(self.a, [
            {'id': self.cake.id, 'quantity': 2, 'price': '1.00', 'unit_price': '1.00'},
            {'id': self.sized.id, 'quantity': 1, 'options': {'size': '1 kg'}},
        ])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['subtotal'], '1800.00')
        self.cake.price = Decimal('600.00'); self.cake.discount = 10; self.cake.save()
        r = self.as_user(self.a).get(URL)
        self.assertEqual(r.data['items'][0]['unit_price'], '540.00')
        self.assertEqual(r.data['subtotal'], '1880.00')
        self.assertEqual(r.data['count'], 3)

    def test_users_are_isolated(self):
        self.put(self.a, [{'id': self.cake.id, 'quantity': 1}])
        self.assertEqual(self.as_user(self.b).get(URL).data['items'], [])
        self.put(self.b, [])
        self.assertEqual(len(self.as_user(self.a).get(URL).data['items']), 1)

    def test_quantity_bounds_and_bad_payloads_rejected(self):
        for qty in (0, -1, CART_QTY_MAX + 1, 'x', 1.5, True):
            r = self.put(self.a, [{'id': self.cake.id, 'quantity': qty}])
            self.assertEqual(r.status_code, 400, qty)
        self.assertEqual(self.as_user(self.a).put(URL, {'items': 'nope'}, format='json').status_code, 400)
        self.assertEqual(self.put(self.a, [{'id': self.sized.id, 'quantity': 1, 'options': {'size': '9 kg'}}]).status_code, 400)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_unknown_product_and_unpriced_size_are_dropped_and_reported(self):
        r = self.put(self.a, [
            {'id': 999999, 'quantity': 1},
            {'id': self.sized.id, 'quantity': 1, 'options': {'size': '0.5 kg'}},
            {'id': self.cake.id, 'quantity': 1},
        ])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data['items']), 1)
        self.assertEqual(len(r.data['removed']), 2)

    def test_stale_lines_removed_on_read(self):
        self.put(self.a, [{'id': self.cake.id, 'quantity': 1}, {'id': self.sized.id, 'quantity': 1, 'options': {'size': '2 kg'}}])
        self.cake.availability = 'out_of_stock'; self.cake.save()
        self.sized.size_prices = {'1 kg': '800.00'}; self.sized.save()
        r = self.as_user(self.a).get(URL)
        self.assertEqual(r.data['items'], [])
        self.assertEqual(len(r.data['removed']), 2)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_expired_delivery_date_line_removed(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.put(self.a, [{'id': self.cake.id, 'quantity': 1, 'options': {'delivery_date': future}}])
        CartItem.objects.update(options={'delivery_date': '2020-01-01'})
        r = self.as_user(self.a).get(URL)
        self.assertEqual(r.data['items'], [])

    def test_quantity_clamped_to_stock(self):
        self.put(self.a, [{'id': self.cake.id, 'quantity': 10}])
        self.cake.available_quantity = 3; self.cake.save()
        r = self.as_user(self.a).get(URL)
        self.assertEqual(r.data['items'][0]['quantity'], 3)
        self.assertEqual(r.data['adjusted'][0]['to'], 3)

    def test_duplicate_lines_collapse_and_options_split(self):
        r = self.put(self.a, [
            {'id': self.cake.id, 'quantity': 1}, {'id': self.cake.id, 'quantity': 2},
            {'id': self.cake.id, 'quantity': 1, 'options': {'message': 'Happy'}},
        ])
        self.assertEqual(sorted(i['quantity'] for i in r.data['items']), [1, 3])

    def test_merge_sums_caps_and_skips_junk(self):
        self.put(self.a, [{'id': self.cake.id, 'quantity': 30}])
        r = self.as_user(self.a).post(MERGE, {'items': [
            {'id': self.cake.id, 'quantity': 25},
            {'id': self.sized.id, 'quantity': 2, 'options': {'size': '1 kg'}},
            {'id': 999999, 'quantity': 1}, 'junk', {'id': self.cake.id, 'quantity': 0},
        ]}, format='json')
        self.assertEqual(r.status_code, 200)
        by_id = {i['id']: i['quantity'] for i in r.data['items']}
        self.assertEqual(by_id, {self.cake.id: CART_QTY_MAX, self.sized.id: 2})
        self.assertEqual(len(r.data['removed']), 3)

    def test_cart_cleared_when_order_paid(self):
        self.put(self.a, [{'id': self.cake.id, 'quantity': 1}])
        self.put(self.b, [{'id': self.cake.id, 'quantity': 1}])
        order = Order.objects.create(
            user=self.a, order_number='PB-CART-1', customer_name='A', customer_email='a@example.com',
            customer_mobile='9876543210', shipping_address='Main Road', city='Mumbai', state='Maharashtra',
            postal_code='400001', country='India', subtotal_amount=Decimal('500.00'), total_amount=Decimal('500.00'),
            status='PENDING', payment_status='pending',
        )
        payment = Payment.objects.create(order=order, user=self.a, gateway='razorpay', gateway_order_id='order_cart_1',
                                         amount=500, currency='INR', status='created')
        PaymentService.mark_payment_paid(payment, 'pay_cart_1', signature='sig')
        self.assertEqual(self.as_user(self.a).get(URL).data['items'], [])
        self.assertEqual(len(self.as_user(self.b).get(URL).data['items']), 1)
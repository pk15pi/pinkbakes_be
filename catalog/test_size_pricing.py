"""BUG-026: per-size prices set in admin, computed server-side."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .models import Coupon, Order, OrderItem, Product


class SizePricingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='sizer', email='sizer@example.com', password='Pass12345!')
        self.admin = User.objects.create_user(username='sizeadmin', password='Pass12345!', is_staff=True, is_superuser=True)
        common = dict(
            category='Chocolate Cakes', is_active=True, status='published', availability='in_stock',
            available_quantity=50, reserved_quantity=0, sold_quantity=0, low_stock_threshold=2,
        )
        self.sized = Product.objects.create(
            name='Sized Truffle', price=Decimal('800.00'),
            size_prices={'0.5 kg': '500.00', '1 kg': '800.00', '2 kg': '1500.00'}, **common,
        )
        self.plain = Product.objects.create(name='Plain Truffle', price=Decimal('800.00'), **common)

    def _payload(self, items, **extra):
        data = {
            'items': items,
            'customer_name': 'Sizer', 'customer_email': 'sizer@example.com', 'customer_mobile': '9876543210',
            'shipping_address': '1 Test Street', 'city': 'Mumbai', 'state': 'Maharashtra',
            'postal_code': '400001', 'country': 'India',
        }
        data.update(extra)
        return data

    def _checkout(self, items, **extra):
        self.client.force_authenticate(user=self.user)
        return self.client.post('/api/orders/checkout/', self._payload(items, **extra), format='json')

    def _items(self, response):
        return list(OrderItem.objects.filter(order_id=response.json()['id']).order_by('id'))

    # --- model helpers -------------------------------------------------
    def test_model_helpers(self):
        self.assertEqual(self.sized.base_price_for_size('2 kg'), Decimal('1500.00'))
        self.assertEqual(self.sized.base_price_for_size(None), Decimal('800.00'))
        self.assertEqual(self.plain.base_price_for_size('2 kg'), Decimal('800.00'))
        with self.assertRaises(ValueError):
            self.sized.base_price_for_size('1.5 kg')

    # --- checkout --------------------------------------------------------
    def test_size_price_is_used(self):
        response = self._checkout([
            {'id': self.sized.id, 'quantity': 2, 'options': {'size': '2 kg'}},
            {'id': self.sized.id, 'quantity': 1, 'options': {'size': '0.5 kg'}},
        ])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        items = self._items(response)
        self.assertEqual([i.unit_price for i in items], [Decimal('1500.00'), Decimal('500.00')])
        self.assertEqual([i.subtotal for i in items], [Decimal('3000.00'), Decimal('500.00')])
        self.assertEqual(Order.objects.get(id=response.json()['id']).subtotal_amount, Decimal('3500.00'))

    def test_product_discount_applies_to_size_price(self):
        self.sized.discount = 10
        self.sized.save()
        response = self._checkout([{'id': self.sized.id, 'quantity': 1, 'options': {'size': '2 kg'}}])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(self._items(response)[0].unit_price, Decimal('1350.00'))

    def test_fallback_to_base_price_without_size_prices(self):
        response = self._checkout([{'id': self.plain.id, 'quantity': 1, 'options': {'size': '2 kg'}}])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(self._items(response)[0].unit_price, Decimal('800.00'))

    def test_fallback_to_base_price_when_no_size_chosen(self):
        response = self._checkout([{'id': self.sized.id, 'quantity': 1}])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(self._items(response)[0].unit_price, Decimal('800.00'))

    def test_unpriced_size_is_rejected(self):
        before = Order.objects.count()
        response = self._checkout([{'id': self.sized.id, 'quantity': 1, 'options': {'size': '1.5 kg'}}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('1.5 kg', response.json()['detail'])
        self.assertIn('not available', response.json()['detail'])
        self.assertEqual(Order.objects.count(), before)

    def test_unknown_size_is_rejected(self):
        response = self._checkout([{'id': self.sized.id, 'quantity': 1, 'options': {'size': '5 kg'}}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_client_sent_price_is_ignored(self):
        response = self._checkout([{
            'id': self.sized.id, 'quantity': 1, 'price': '1.00', 'unit_price': '1.00',
            'discounted_price': '1.00', 'size_prices': {'2 kg': '1.00'}, 'options': {'size': '2 kg', 'price': '1.00'},
        }])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        item = self._items(response)[0]
        self.assertEqual(item.unit_price, Decimal('1500.00'))
        self.assertEqual(item.options, {'size': '2 kg'})

    def test_coupon_applies_on_size_price(self):
        Coupon.objects.create(
            code='SIZE10', name='Size 10', discount_type='percentage', discount_value=Decimal('10'),
            minimum_order_amount=Decimal('0'), is_active=True, applies_to='all',
        )
        self.client.force_authenticate(user=self.user)
        preview = self.client.post('/api/coupons/validate/', {
            'code': 'SIZE10', 'items': [{'id': self.sized.id, 'quantity': 1, 'options': {'size': '2 kg'}}],
        }, format='json')
        self.assertEqual(preview.status_code, status.HTTP_200_OK, preview.content)
        self.assertEqual(float(preview.json()['discount_amount']), 150.0)
        response = self._checkout(
            [{'id': self.sized.id, 'quantity': 1, 'options': {'size': '2 kg'}}], coupon_code='SIZE10',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        order = Order.objects.get(id=response.json()['id'])
        self.assertEqual(order.subtotal_amount, Decimal('1500.00'))
        self.assertEqual(order.coupon_discount_amount, Decimal('150.00'))

    def test_coupon_preview_rejects_unpriced_size(self):
        Coupon.objects.create(
            code='SIZE10', name='Size 10', discount_type='percentage', discount_value=Decimal('10'),
            minimum_order_amount=Decimal('0'), is_active=True, applies_to='all',
        )
        self.client.force_authenticate(user=self.user)
        preview = self.client.post('/api/coupons/validate/', {
            'code': 'SIZE10', 'items': [{'id': self.sized.id, 'quantity': 1, 'options': {'size': '1.5 kg'}}],
        }, format='json')
        self.assertFalse(preview.json()['valid'])

    # --- public API ------------------------------------------------------
    def test_public_list_and_detail_expose_size_prices(self):
        listing = self.client.get('/api/products/')
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        body = listing.json()
        rows = body['results'] if isinstance(body, dict) else body
        row = next(r for r in rows if r['id'] == self.sized.id)
        self.assertEqual(row['size_prices'], {'0.5 kg': '500.00', '1 kg': '800.00', '2 kg': '1500.00'})
        detail = self.client.get(f'/api/products/{self.sized.id}/')
        self.assertEqual(detail.json()['size_prices']['2 kg'], '1500.00')

    # --- admin API -------------------------------------------------------
    def _admin_put(self, product, size_prices):
        self.client.force_authenticate(user=self.admin)
        return self.client.put(f'/api/catalog/admin/products/{product.id}/', {
            'name': product.name, 'category': product.category, 'price': '800.00', 'discount': 0,
            'description': 'x', 'short_description': 'x', 'availability': 'in_stock', 'status': 'published',
            'size_prices': size_prices,
        }, format='json')

    def test_admin_sets_size_prices(self):
        response = self._admin_put(self.plain, {'2 kg': 1499.5, '0.5 kg': '450', '1 kg': '', '1.5 kg': None})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.size_prices, {'0.5 kg': '450.00', '2 kg': '1499.50'})
        self.assertEqual(response.json()['size_prices'], {'0.5 kg': '450.00', '2 kg': '1499.50'})

    def test_admin_can_clear_size_prices(self):
        response = self._admin_put(self.sized, {})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.sized.refresh_from_db()
        self.assertEqual(self.sized.size_prices, {})

    def test_admin_put_without_size_prices_keeps_them(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.put(f'/api/catalog/admin/products/{self.sized.id}/', {
            'name': self.sized.name, 'price': '800.00', 'description': 'x',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.sized.refresh_from_db()
        self.assertEqual(self.sized.size_prices['2 kg'], '1500.00')

    def test_admin_create_with_size_prices(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/catalog/admin/products/', {
            'name': 'New Sized Cake', 'category': 'Birthday Cakes', 'price': '700', 'description': 'x',
            'size_prices': {'1 kg': '700', '2 kg': '1300'},
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(Product.objects.get(id=response.json()['id']).size_prices, {'1 kg': '700.00', '2 kg': '1300.00'})

    def test_admin_invalid_size_prices_rejected(self):
        bad_values = [
            {'3 kg': '900'},
            {'1 kg': '-5'},
            {'1 kg': '0'},
            {'1 kg': 'abc'},
            {'1 kg': 'NaN'},
            {'1 kg': '10.999'},
            {'1 kg': True},
            ['1 kg', '900'],
        ]
        for bad in bad_values:
            response = self._admin_put(self.sized, bad)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, (bad, response.content))
            self.assertIn('size_prices', response.json())
        self.sized.refresh_from_db()
        self.assertEqual(self.sized.size_prices['2 kg'], '1500.00')

    def test_customer_cannot_set_size_prices(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.put(f'/api/catalog/admin/products/{self.sized.id}/', {
            'name': self.sized.name, 'price': '1', 'description': 'x', 'size_prices': {'2 kg': '1'},
        }, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.sized.refresh_from_db()
        self.assertEqual(self.sized.size_prices['2 kg'], '1500.00')

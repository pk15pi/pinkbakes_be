"""Phase 16: wishlist, review moderation + pagination, coupon admin edit/toggle, order isolation."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Coupon, Order, OrderItem, Product, Review

PW = 'Pass-word-123!'
COMMON = dict(category='Birthday Cakes', is_active=True, status='published', availability='in_stock', available_quantity=50)


def make_order(user, n, status='ORDER_CONFIRMED', product=None):
    order = Order.objects.create(
        user=user, order_number=f'P16-{user.username}-{n}', customer_name='Test', customer_email='t@example.com',
        customer_mobile='9000000000', shipping_address='1 Test Street', city='Testville', state='TS',
        postal_code='100001', country='India', status=status, total_amount=Decimal('500.00'),
    )
    if product is not None:
        OrderItem.objects.create(order=order, product=product, product_name=product.name,
                                 unit_price=product.price, quantity=1, subtotal=product.price)
    return order


class Base(TestCase):
    def setUp(self):
        self.a = User.objects.create_user(username='p16_a', email='a@example.com', password=PW)
        self.b = User.objects.create_user(username='p16_b', email='b@example.com', password=PW)
        self.staff = User.objects.create_user(username='p16_staff', email='s@example.com', password=PW, is_staff=True)
        self.cake = Product.objects.create(name='P16 Cake', price=Decimal('500.00'), **COMMON)
        self.cake2 = Product.objects.create(name='P16 Cake Two', price=Decimal('650.00'), **COMMON)

    def client_for(self, user=None):
        c = APIClient()
        if user is not None:
            c.force_authenticate(user=user)
        return c


class WishlistTests(Base):
    def test_auth_required(self):
        anon = self.client_for()
        self.assertIn(anon.get('/api/wishlist/').status_code, (401, 403))
        self.assertIn(anon.post('/api/wishlist/items/', {'id': self.cake.id}, format='json').status_code, (401, 403))

    def test_add_list_remove_and_isolation(self):
        a = self.client_for(self.a)
        r = a.post('/api/wishlist/items/', {'id': self.cake.id}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual([i['id'] for i in r.data['items']], [self.cake.id])
        self.assertEqual(a.post('/api/wishlist/items/', {'id': self.cake.id}, format='json').data['count'], 1)
        self.assertEqual(self.client_for(self.b).get('/api/wishlist/').data['items'], [])
        r = a.delete(f'/api/wishlist/items/{self.cake.id}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['items'], [])

    def test_unknown_product_rejected(self):
        r = self.client_for(self.a).post('/api/wishlist/items/', {'id': 999999}, format='json')
        self.assertIn(r.status_code, (400, 404))

    def test_merge_guest_list_dedupes_and_drops_unknown(self):
        a = self.client_for(self.a)
        a.post('/api/wishlist/items/', {'id': self.cake.id}, format='json')
        r = a.post('/api/wishlist/merge/', {'items': [self.cake.id, self.cake2.id, 999999]}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(sorted(i['id'] for i in r.data['items']), sorted([self.cake.id, self.cake2.id]))
        self.assertEqual(r.data['count'], 2)


class ReviewModerationTests(Base):
    URL = '/api/products/{}/reviews/'

    def test_review_needs_delivered_order_and_starts_pending(self):
        a = self.client_for(self.a)
        url = self.URL.format(self.cake.id)
        self.assertEqual(a.post(url, {'rating': 5, 'comment': 'Lovely'}, format='json').status_code, 400)
        make_order(self.a, 1, status='DELIVERED', product=self.cake)
        page = a.get(url, {'page': 1, 'page_size': 10}).data
        self.assertTrue(page['can_review'])
        r = a.post(url, {'rating': 5, 'comment': 'Lovely cake'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        review = Review.objects.get()
        self.assertEqual(review.status, 'pending')
        # Public sees nothing; author sees it in `mine`; others don't.
        self.assertEqual(self.client_for().get(url, {'page': 1}).data['results'], [])
        mine = a.get(url, {'page': 1}).data
        self.assertEqual(mine['results'], [])
        self.assertEqual(len(mine['mine']), 1)
        self.assertFalse(mine['can_review'])
        self.assertEqual(self.client_for(self.b).get(url, {'page': 1}).data['mine'], [])
        # Non-staff cannot approve; staff can, then it is public.
        self.assertIn(a.post(f'/api/admin/reviews/{review.id}/approve/').status_code, (401, 403))
        self.assertEqual(self.client_for(self.staff).post(f'/api/admin/reviews/{review.id}/approve/', {}, format='json').status_code, 200)
        public = self.client_for().get(url, {'page': 1}).data
        self.assertEqual(len(public['results']), 1)
        self.assertEqual(a.get(url, {'page': 1}).data['mine'], [])

    def test_rejected_review_is_never_public(self):
        make_order(self.a, 1, status='DELIVERED', product=self.cake)
        self.client_for(self.a).post(self.URL.format(self.cake.id), {'rating': 2, 'comment': 'Not great this time'}, format='json')
        review = Review.objects.get()
        self.client_for(self.staff).post(f'/api/admin/reviews/{review.id}/reject/', {'reason': 'spam'}, format='json')
        self.assertEqual(self.client_for().get(self.URL.format(self.cake.id), {'page': 1}).data['results'], [])

    def test_pagination(self):
        for i in range(23):
            Review.objects.create(product=self.cake, name=f'R{i}', rating=4, comment=f'c{i}', status='approved')
        Review.objects.create(product=self.cake, name='hidden', rating=1, comment='x', status='pending')
        url = self.URL.format(self.cake.id)
        p1 = self.client_for().get(url, {'page': 1, 'page_size': 10}).data
        self.assertEqual((p1['count'], p1['total_pages'], p1['has_next'], len(p1['results'])), (23, 3, True, 10))
        p3 = self.client_for().get(url, {'page': 3, 'page_size': 10}).data
        self.assertEqual((len(p3['results']), p3['has_next']), (3, False))
        ids = [r['id'] for r in p1['results']] + [r['id'] for r in self.client_for().get(url, {'page': 2, 'page_size': 10}).data['results']] + [r['id'] for r in p3['results']]
        self.assertEqual(len(set(ids)), 23)
        big = self.client_for().get(url, {'page': 1, 'page_size': 500}).data
        self.assertLessEqual(len(big['results']), 50)
        # Legacy unpaginated shape still a list of approved only.
        legacy = self.client_for().get(url).data
        self.assertEqual(len(legacy), 23)


class CouponAdminTests(Base):
    LIST = '/api/admin/coupons/'

    def test_create_edit_deactivate_activate(self):
        s = self.client_for(self.staff)
        r = s.post(self.LIST, {'code': 'P16SAVE', 'discount_type': 'percentage', 'discount_value': '10'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        cid = r.data['id']
        r = s.patch(f'{self.LIST}{cid}/', {'name': 'Edited', 'discount_value': '15'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        c = Coupon.objects.get(id=cid)
        self.assertEqual((c.name, c.discount_value), ('Edited', Decimal('15.00')))
        self.assertFalse(s.patch(f'{self.LIST}{cid}/', {'is_active': False}, format='json').data['is_active'])
        self.assertTrue(s.patch(f'{self.LIST}{cid}/', {'is_active': True}, format='json').data['is_active'])

    def test_customers_cannot_manage_coupons(self):
        c = Coupon.objects.create(code='P16X', discount_value=Decimal('5'))
        a = self.client_for(self.a)
        self.assertIn(a.get(self.LIST).status_code, (401, 403))
        self.assertIn(a.patch(f'{self.LIST}{c.id}/', {'is_active': False}, format='json').status_code, (401, 403))
        self.assertIn(self.client_for().post(self.LIST, {'code': 'NOPE'}, format='json').status_code, (401, 403))


class OrderHistoryTests(Base):
    def test_paginated_and_isolated(self):
        for i in range(12):
            make_order(self.a, i, product=self.cake)
        make_order(self.b, 1, product=self.cake2)
        a = self.client_for(self.a)
        p1 = a.get('/api/orders/', {'page': 1, 'page_size': 5}).data
        self.assertEqual((p1['count'], p1['has_next'], len(p1['results'])), (12, True, 5))
        p3 = a.get('/api/orders/', {'page': 3, 'page_size': 5}).data
        self.assertEqual((len(p3['results']), p3['has_next']), (2, False))
        self.assertTrue(all(o['order_number'].startswith('P16-p16_a-') for o in p1['results'] + p3['results']))
        self.assertEqual(self.client_for(self.b).get('/api/orders/', {'page': 1}).data['count'], 1)
        other = Order.objects.get(user=self.b)
        self.assertEqual(a.get(f'/api/orders/{other.id}/').status_code, 403)
        self.assertIn(self.client_for().get('/api/orders/').status_code, (401, 403))
        self.assertEqual(a.get('/api/orders/').data['count'], 12)

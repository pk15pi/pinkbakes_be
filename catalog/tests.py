import hashlib
import json
import hmac
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone
from django.test import TestCase, TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import Coupon, CouponRedemption, InventoryTransaction, Order, OrderItem, OrderStatusHistory, Payment, Product, Refund, Review, AdminActivity, Employee, EmployeeCategory
from . import inventory as inventory_service


def build_razorpay_signature(order_id, payment_id, secret):
    payload = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


class CatalogAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='admin', password='securepass123', is_staff=True, is_superuser=True)
        self.product = Product.objects.create(
            name='Chocolate Truffle Cake',
            category='Chocolate Cakes',
            price=Decimal('1200.00'),
            discount=10,
            short_description='Rich and decadent',
            description='A full chocolate experience.',
            main_image='https://example.com/cake.jpg',
            availability='in_stock',
            status='published',
            is_active=True,
            available_quantity=50,
            reserved_quantity=0,
            sold_quantity=0,
            low_stock_threshold=5,
        )

    def test_order_checkout_creates_order_and_items_for_authenticated_user(self):
        user = User.objects.create_user(username='customer', email='customer@example.com', password='securepass123')
        self.client.force_authenticate(user=user)

        response = self.client.post('/api/orders/checkout/', {
            'items': [
                {'id': self.product.id, 'quantity': 2},
            ],
            'customer_name': 'Customer Name',
            'customer_email': 'customer@example.com',
            'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road',
            'shipping_address_2': 'Near Station',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertIn('order_number', data)
        self.assertEqual(float(data['total_amount']), 2160.0)
        self.assertEqual(data['items'][0]['product_name'], 'Chocolate Truffle Cake')
        self.assertEqual(data['items'][0]['quantity'], 2)

    def test_order_history_returns_only_current_user_orders(self):
        user_a = User.objects.create_user(username='customer_a', email='a@example.com', password='pass12345')
        user_b = User.objects.create_user(username='customer_b', email='b@example.com', password='pass12345')
        self.client.force_authenticate(user=user_a)

        order_payload = {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'customer_name': 'A',
            'customer_email': 'a@example.com',
            'customer_mobile': '1111111111',
            'shipping_address': 'A Street',
            'city': 'City',
            'state': 'State',
            'postal_code': '123456',
            'country': 'India',
        }
        first = self.client.post('/api/orders/checkout/', order_payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)

        self.client.force_authenticate(user=user_b)
        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['count'], 0)

    def test_guest_user_cannot_checkout(self):
        response = self.client.post('/api/orders/checkout/', {'items': [{'id': self.product.id, 'quantity': 1}]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_payment_creation_returns_gateway_details_for_authenticated_user(self):
        user = User.objects.create_user(username='payer', email='payer@example.com', password='securepass123')
        self.client.force_authenticate(user=user)

        response = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 2}],
            'customer_name': 'Payer Name',
            'customer_email': 'payer@example.com',
            'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['gateway'], 'razorpay')
        self.assertEqual(data['currency'], 'INR')
        self.assertEqual(data['amount'], 216000)
        self.assertIn('payment_order_id', data)

    def test_payment_verification_marks_order_paid_only_for_valid_signature(self):
        user = User.objects.create_user(username='verifier', email='verifier@example.com', password='securepass123')
        self.client.force_authenticate(user=user)

        created = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'customer_name': 'Verifier',
            'customer_email': 'verifier@example.com',
            'customer_mobile': '9876543211',
            'shipping_address': '7 Street',
            'city': 'Pune',
            'state': 'Maharashtra',
            'postal_code': '411001',
            'country': 'India',
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_200_OK)
        payment_data = created.json()

        order_id = payment_data['payment_order_id']
        payment_id = 'pay_test_123'
        signature = build_razorpay_signature(order_id, payment_id, 'test_razorpay_secret')

        response = self.client.post('/api/payments/verify/', {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['status'], 'paid')

    def test_product_listing_returns_discounted_price_and_rating(self):
        Review.objects.create(product=self.product, user=self.admin, name='Admin', rating=5, comment='Great cake', status='approved')
        response = self.client.get('/api/catalog/products/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertGreater(len(data), 0)
        product_data = data[0]
        self.assertEqual(float(product_data['discounted_price']), 1080.0)
        self.assertEqual(product_data['average_rating'], 5.0)
        self.assertEqual(product_data['review_count'], 1)

    def test_admin_login_works_for_staff_user(self):
        response = self.client.post('/api/catalog/admin/login/', {'username': 'admin', 'password': 'securepass123'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.json())

    def test_review_requires_authentication(self):
        response = self.client.post(f'/api/catalog/products/{self.product.id}/reviews/', {'rating': 5, 'comment': 'Excellent'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_customer_can_submit_review_only_after_delivered_order(self):
        user = User.objects.create_user(username='reviewer', email='reviewer@example.com', password='securepass123')
        order = Order.objects.create(
            user=user,
            order_number='PB-TEST-001',
            customer_name='Reviewer',
            customer_email='reviewer@example.com',
            customer_mobile='9876543210',
            shipping_address='Main Road',
            city='Mumbai',
            state='Maharashtra',
            postal_code='400001',
            country='India',
            subtotal_amount=Decimal('1200.00'),
            total_amount=Decimal('1200.00'),
            status='DELIVERED',
        )
        order_item = OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            product_image=self.product.main_image,
            unit_price=self.product.discounted_price,
            quantity=1,
            subtotal=self.product.discounted_price,
        )

        self.client.force_authenticate(user=user)
        response = self.client.post(
            f'/api/catalog/products/{self.product.id}/reviews/',
            {'rating': 5, 'comment': 'Excellent cake!'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data['review']['status'], 'pending')
        self.assertEqual(data['review']['order_item'], order_item.id)

    def test_customer_cannot_review_product_from_non_delivered_order(self):
        user = User.objects.create_user(username='pending_user', password='securepass123')
        order = Order.objects.create(
            user=user,
            order_number='PB-TEST-002',
            customer_name='Pending User',
            customer_email='pending@example.com',
            customer_mobile='9876543211',
            shipping_address='Second Road',
            city='Pune',
            state='Maharashtra',
            postal_code='411001',
            country='India',
            subtotal_amount=Decimal('1200.00'),
            total_amount=Decimal('1200.00'),
            status='ORDER_CONFIRMED',
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            product_image=self.product.main_image,
            unit_price=self.product.discounted_price,
            quantity=1,
            subtotal=self.product.discounted_price,
        )

        self.client.force_authenticate(user=user)
        response = self.client.post(
            f'/api/catalog/products/{self.product.id}/reviews/',
            {'rating': 5, 'comment': 'Not allowed yet'},
            format='json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_can_approve_review_and_make_it_visible(self):
        user = User.objects.create_user(username='reviewer2', password='securepass123')
        order = Order.objects.create(
            user=user,
            order_number='PB-TEST-003',
            customer_name='Reviewer 2',
            customer_email='reviewer2@example.com',
            customer_mobile='9876543212',
            shipping_address='Third Road',
            city='Delhi',
            state='Delhi',
            postal_code='110001',
            country='India',
            subtotal_amount=Decimal('1200.00'),
            total_amount=Decimal('1200.00'),
            status='DELIVERED',
        )
        order_item = OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            product_image=self.product.main_image,
            unit_price=self.product.discounted_price,
            quantity=1,
            subtotal=self.product.discounted_price,
        )
        review = Review.objects.create(
            product=self.product,
            user=user,
            name='Reviewer 2',
            order=order,
            order_item=order_item,
            rating=5,
            comment='Amazing cake',
            status='pending',
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(f'/api/catalog/admin/reviews/{review.id}/approve/', {'admin_comment': 'Looks good.'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        review.refresh_from_db()
        self.assertEqual(review.status, 'approved')
        self.assertEqual(review.approved_by_id, self.admin.id)

        public = self.client.get(f'/api/catalog/products/{self.product.id}/reviews/')
        self.assertEqual(public.status_code, status.HTTP_200_OK)
        self.assertEqual(len(public.json()), 1)

    def test_admin_can_reject_review(self):
        user = User.objects.create_user(username='reviewer3', password='securepass123')
        order = Order.objects.create(
            user=user,
            order_number='PB-TEST-004',
            customer_name='Reviewer 3',
            customer_email='reviewer3@example.com',
            customer_mobile='9876543213',
            shipping_address='Fourth Road',
            city='Bengaluru',
            state='Karnataka',
            postal_code='560001',
            country='India',
            subtotal_amount=Decimal('1200.00'),
            total_amount=Decimal('1200.00'),
            status='DELIVERED',
        )
        order_item = OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            product_image=self.product.main_image,
            unit_price=self.product.discounted_price,
            quantity=1,
            subtotal=self.product.discounted_price,
        )
        review = Review.objects.create(
            product=self.product,
            user=user,
            name='Reviewer 3',
            order=order,
            order_item=order_item,
            rating=1,
            comment='Spam review',
            status='pending',
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(f'/api/catalog/admin/reviews/{review.id}/reject/', {'admin_comment': 'Irrelevant content.'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        review.refresh_from_db()
        self.assertEqual(review.status, 'rejected')
        self.assertEqual(review.admin_comment, 'Irrelevant content.')

    def test_admin_can_update_product(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.put(
            f'/api/catalog/admin/products/{self.product.id}/',
            {
                'name': 'Updated Chocolate Truffle',
                'category': 'Designer Cakes',
                'price': '1500.00',
                'discount': 15,
                'short_description': 'Updated flavor',
                'description': 'Updated product description.',
                'main_image': 'https://example.com/updated.jpg',
                'availability': 'low_stock',
                'status': 'published',
                'is_active': True,
            },
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Updated Chocolate Truffle')
        self.assertEqual(float(self.product.price), 1500.00)
        self.assertEqual(self.product.category, 'Designer Cakes')

    def test_admin_can_access_reports_summary(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/admin/reports/summary/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('user_stats', data)
        self.assertIn('product_stats', data)
        self.assertIn('review_stats', data)
        self.assertIn('sales_stats', data)
        self.assertEqual(data['sales_stats']['total_orders'], 0)

    def test_non_admin_cannot_access_reports_summary(self):
        customer = User.objects.create_user(username='customer', password='password123')
        self.client.force_authenticate(user=customer)
        response = self.client.get('/api/admin/reports/summary/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


    def _create_payment_session(self, user, quantity=1):
        self.client.force_authenticate(user=user)
        response = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': quantity}],
            'customer_name': user.get_full_name() or user.username,
            'customer_email': user.email or f'{user.username}@example.com',
            'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()

    def test_unauthenticated_payment_create_fails(self):
        response = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'customer_name': 'Guest',
            'customer_email': 'guest@example.com',
            'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_payment_amount_mismatch_fails(self):
        user = User.objects.create_user(username='amt_user', email='amt@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        order_id = payment_data['payment_order_id']
        payment_id = 'pay_amt_1'
        signature = build_razorpay_signature(order_id, payment_id, 'test_razorpay_secret')

        response = self.client.post('/api/payments/verify/', {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
            'amount': payment_data['amount'] + 100,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        payment = Payment.objects.get(gateway_order_id=order_id)
        self.assertEqual(payment.status, 'failed')

    def test_duplicate_payment_verify_is_idempotent(self):
        user = User.objects.create_user(username='dup_user', email='dup@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        order_id = payment_data['payment_order_id']
        payment_id = 'pay_dup_1'
        signature = build_razorpay_signature(order_id, payment_id, 'test_razorpay_secret')
        payload = {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
            'amount': payment_data['amount'],
        }
        first = self.client.post('/api/payments/verify/', payload, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post('/api/payments/verify/', payload, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.json()['status'], 'paid')
        payment = Payment.objects.get(gateway_order_id=order_id)
        self.assertEqual(payment.status, 'paid')
        self.assertEqual(OrderStatusHistory.objects.filter(order_id=payment_data['order_id'], status='ORDER_CONFIRMED').count(), 1)

    def test_webhook_valid_invalid_and_duplicate(self):
        user = User.objects.create_user(username='hook_user', email='hook@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        gateway_order_id = payment_data['payment_order_id']
        payment_id = 'pay_hook_1'

        body_obj = {
            'event': 'payment.captured',
            'payload': {
                'payment': {
                    'entity': {
                        'id': payment_id,
                        'order_id': gateway_order_id,
                        'method': 'upi',
                    }
                }
            }
        }
        body = json.dumps(body_obj, separators=(',', ':'))
        good_sig = hmac.new(b'test_webhook_secret', body.encode(), hashlib.sha256).hexdigest()

        bad = self.client.post(
            '/api/payments/webhook/razorpay/',
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE='bad-signature',
        )
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

        first = self.client.post(
            '/api/payments/webhook/razorpay/',
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=good_sig,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        payment = Payment.objects.get(gateway_order_id=gateway_order_id)
        self.assertEqual(payment.status, 'paid')
        order = Order.objects.get(id=payment_data['order_id'])
        self.assertEqual(order.payment_status, 'paid')
        self.assertEqual(order.status, 'ORDER_CONFIRMED')
        self.assertTrue(OrderStatusHistory.objects.filter(order=order, status='ORDER_CONFIRMED').exists())

        second = self.client.post(
            '/api/payments/webhook/razorpay/',
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=good_sig,
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(OrderStatusHistory.objects.filter(order=order, status='ORDER_CONFIRMED').count(), 1)

    def test_retry_payment_creates_new_gateway_order_id(self):
        user = User.objects.create_user(username='retry_user', email='retry@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        old_gateway_id = payment_data['payment_order_id']
        order_id = payment_data['order_id']

        response = self.client.post(f'/api/orders/{order_id}/retry-payment/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        new_data = response.json()
        self.assertIn('payment_order_id', new_data)
        self.assertNotEqual(new_data['payment_order_id'], old_gateway_id)
        self.assertEqual(Payment.objects.filter(order_id=order_id).count(), 2)
        old_payment = Payment.objects.get(gateway_order_id=old_gateway_id)
        self.assertEqual(old_payment.status, 'cancelled')
        new_payment = Payment.objects.get(gateway_order_id=new_data['payment_order_id'])
        self.assertEqual(new_payment.status, 'created')

    def test_customer_cannot_access_other_order_payments(self):
        owner = User.objects.create_user(username='owner_pay', email='owner@example.com', password='securepass123')
        other = User.objects.create_user(username='other_pay', email='other@example.com', password='securepass123')
        payment_data = self._create_payment_session(owner, quantity=1)
        order_id = payment_data['order_id']

        self.client.force_authenticate(user=other)
        response = self.client.get(f'/api/orders/{order_id}/payments/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_order_checkout_uses_pending_status_choice(self):
        user = User.objects.create_user(username='checkout_status', email='cs@example.com', password='securepass123')
        self.client.force_authenticate(user=user)
        response = self.client.post('/api/orders/checkout/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'customer_name': 'Checkout Status',
            'customer_email': 'cs@example.com',
            'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()['status'], 'PENDING')


    def _pay_created_session(self, payment_data, payment_id='pay_cancel_1'):
        order_id = payment_data['payment_order_id']
        signature = build_razorpay_signature(order_id, payment_id, 'test_razorpay_secret')
        response = self.client.post('/api/payments/verify/', {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
            'amount': payment_data['amount'],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()

    def test_customer_can_cancel_eligible_order(self):
        user = User.objects.create_user(username='cancel_ok', email='cancel_ok@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        order = Order.objects.get(id=payment_data['order_id'])
        self.assertTrue(order.is_customer_cancellable())

        response = self.client.post(f'/api/orders/{order.id}/cancel/', {'reason': 'Changed mind'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, 'CANCELLED')
        self.assertEqual(order.cancellation_reason, 'Changed mind')
        self.assertEqual(order.cancelled_by_id, user.id)
        self.assertIsNotNone(order.cancelled_at)
        self.assertTrue(OrderStatusHistory.objects.filter(order=order, status='CANCELLED').exists())
        self.assertEqual(response.json().get('cancellable'), False)

    def test_customer_cannot_cancel_ineligible_order(self):
        user = User.objects.create_user(username='cancel_no', email='cancel_no@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        self._pay_created_session(payment_data, payment_id='pay_inelig_1')
        order = Order.objects.get(id=payment_data['order_id'])
        order.status = 'OUT_FOR_DELIVERY'
        order.save(update_fields=['status'])

        response = self.client.post(f'/api/orders/{order.id}/cancel/', {'reason': 'Too late'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()
        self.assertEqual(order.status, 'OUT_FOR_DELIVERY')

    def test_other_user_cannot_cancel_order(self):
        owner = User.objects.create_user(username='cancel_owner', email='cown@example.com', password='securepass123')
        other = User.objects.create_user(username='cancel_other', email='coth@example.com', password='securepass123')
        payment_data = self._create_payment_session(owner, quantity=1)
        order_id = payment_data['order_id']

        self.client.force_authenticate(user=other)
        response = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'Nope'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_double_cancel_is_idempotent_conflict(self):
        user = User.objects.create_user(username='cancel_dup', email='cdup@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        order_id = payment_data['order_id']
        first = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'First'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'Second'}, format='json')
        self.assertIn(second.status_code, (status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT))
        self.assertEqual(Refund.objects.filter(order_id=order_id).count(), 0)

    def test_unpaid_cancel_does_not_create_refund(self):
        user = User.objects.create_user(username='cancel_unpaid', email='cunp@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        order_id = payment_data['order_id']
        response = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'Unpaid cancel'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Refund.objects.filter(order_id=order_id).count(), 0)
        payment = Payment.objects.get(gateway_order_id=payment_data['payment_order_id'])
        self.assertEqual(payment.status, 'cancelled')

    def test_paid_cancel_creates_refund(self):
        user = User.objects.create_user(username='cancel_paid', email='cpaid@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        self._pay_created_session(payment_data, payment_id='pay_refund_1')
        order_id = payment_data['order_id']

        response = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'Paid cancel'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Refund.objects.filter(order_id=order_id).count(), 1)
        refund = Refund.objects.get(order_id=order_id)
        self.assertEqual(refund.status, 'completed')
        self.assertEqual(refund.initiated_by_type, 'customer')
        payment = Payment.objects.get(gateway_order_id=payment_data['payment_order_id'])
        self.assertEqual(payment.status, 'refunded')
        order = Order.objects.get(id=order_id)
        self.assertEqual(order.status, 'CANCELLED')
        self.assertEqual(order.payment_status, 'refunded')

    def test_admin_partial_refund_and_amount_validation(self):
        user = User.objects.create_user(username='pref_user', email='pref@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=2)
        self._pay_created_session(payment_data, payment_id='pay_partial_1')
        order_id = payment_data['order_id']
        payment = Payment.objects.get(gateway_order_id=payment_data['payment_order_id'])

        self.client.force_authenticate(user=self.admin)
        too_much = self.client.post(
            f'/api/admin/orders/{order_id}/refund/',
            {'amount': str(payment.amount + Decimal('1.00')), 'reason': 'Too much'},
            format='json',
        )
        self.assertEqual(too_much.status_code, status.HTTP_400_BAD_REQUEST)

        half = (payment.amount / 2).quantize(Decimal('0.01'))
        ok = self.client.post(
            f'/api/admin/orders/{order_id}/refund/',
            {'amount': str(half), 'reason': 'Partial'},
            format='json',
        )
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        refund = Refund.objects.get(order_id=order_id)
        self.assertEqual(refund.amount, half)
        self.assertEqual(refund.status, 'completed')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'partially_refunded')

        dup = self.client.post(
            f'/api/admin/orders/{order_id}/refund/',
            {'amount': str(half), 'reason': 'Dup'},
            format='json',
        )
        self.assertEqual(dup.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_full_refund_prevented(self):
        user = User.objects.create_user(username='dup_ref', email='dupref@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        self._pay_created_session(payment_data, payment_id='pay_dupref_1')
        order_id = payment_data['order_id']

        self.client.force_authenticate(user=self.admin)
        first = self.client.post(f'/api/admin/orders/{order_id}/refund/', {'reason': 'Full'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post(f'/api/admin/orders/{order_id}/refund/', {'reason': 'Full again'}, format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Refund.objects.filter(order_id=order_id).count(), 1)

    def test_failed_refund_leaves_payment_paid(self):
        from unittest.mock import patch

        user = User.objects.create_user(username='fail_ref', email='failref@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        self._pay_created_session(payment_data, payment_id='pay_failref_1')
        order = Order.objects.get(id=payment_data['order_id'])
        payment = Payment.objects.get(gateway_order_id=payment_data['payment_order_id'])

        with patch('catalog.views.PaymentService.should_use_live_razorpay', return_value=True):
            with patch('razorpay.Client') as mock_client_cls:
                mock_client = mock_client_cls.return_value
                mock_client.payment.refund.side_effect = Exception('gateway down')
                from catalog.views import PaymentService
                refund = PaymentService.create_refund(
                    payment,
                    amount=payment.amount,
                    reason='Force fail',
                    initiated_by=user,
                    initiated_by_type='customer',
                )
        self.assertEqual(refund.status, 'failed')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'paid')
        order.refresh_from_db()
        self.assertNotEqual(order.payment_status, 'refunded')

    def test_webhook_refund_events_valid_invalid_duplicate(self):
        user = User.objects.create_user(username='hook_ref', email='hookref@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        self._pay_created_session(payment_data, payment_id='pay_hookref_1')
        order_id = payment_data['order_id']
        payment = Payment.objects.get(gateway_order_id=payment_data['payment_order_id'])

        refund = Refund.objects.create(
            order_id=order_id,
            payment=payment,
            user=user,
            gateway='razorpay',
            gateway_refund_id='rfnd_hook_1',
            amount=payment.amount,
            currency='INR',
            reason='Webhook test',
            status='processing',
            initiated_by=user,
            initiated_by_type='customer',
        )
        payment.status = 'refund_pending'
        payment.save(update_fields=['status'])

        body_obj = {
            'event': 'refund.processed',
            'payload': {
                'refund': {
                    'entity': {
                        'id': 'rfnd_hook_1',
                        'payment_id': payment.gateway_payment_id,
                        'amount': int(payment.amount * 100),
                        'status': 'processed',
                    }
                }
            }
        }
        body = json.dumps(body_obj, separators=(',', ':'))
        good_sig = hmac.new(b'test_webhook_secret', body.encode(), hashlib.sha256).hexdigest()

        bad = self.client.post(
            '/api/payments/webhook/razorpay/',
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE='bad',
        )
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

        first = self.client.post(
            '/api/payments/webhook/razorpay/',
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=good_sig,
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'completed')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'refunded')

        second = self.client.post(
            '/api/payments/webhook/razorpay/',
            data=body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=good_sig,
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(Refund.objects.filter(order_id=order_id, status='completed').count(), 1)

        refund2 = Refund.objects.create(
            order_id=order_id,
            payment=payment,
            user=user,
            gateway='razorpay',
            gateway_refund_id='rfnd_hook_fail',
            amount=Decimal('1.00'),
            currency='INR',
            status='processing',
            initiated_by=user,
            initiated_by_type='admin',
        )
        payment.status = 'refund_pending'
        payment.save(update_fields=['status'])
        fail_body_obj = {
            'event': 'refund.failed',
            'payload': {
                'refund': {
                    'entity': {
                        'id': 'rfnd_hook_fail',
                        'payment_id': payment.gateway_payment_id,
                        'amount': 100,
                        'error_description': 'Bank rejected',
                    }
                }
            }
        }
        fail_body = json.dumps(fail_body_obj, separators=(',', ':'))
        fail_sig = hmac.new(b'test_webhook_secret', fail_body.encode(), hashlib.sha256).hexdigest()
        failed = self.client.post(
            '/api/payments/webhook/razorpay/',
            data=fail_body,
            content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=fail_sig,
        )
        self.assertEqual(failed.status_code, status.HTTP_200_OK)
        refund2.refresh_from_db()
        self.assertEqual(refund2.status, 'failed')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'paid')

    def test_customer_cannot_hit_admin_cancel_or_refund(self):
        user = User.objects.create_user(username='cust_admin_block', email='cab@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        order_id = payment_data['order_id']
        self.client.force_authenticate(user=user)
        cancel = self.client.post(f'/api/admin/orders/{order_id}/cancel/', {'reason': 'nope'}, format='json')
        self.assertEqual(cancel.status_code, status.HTTP_403_FORBIDDEN)
        refund = self.client.post(f'/api/admin/orders/{order_id}/refund/', {'reason': 'nope'}, format='json')
        self.assertEqual(refund.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_cancel_preparing_order(self):
        user = User.objects.create_user(username='adm_cancel', email='admc@example.com', password='securepass123')
        payment_data = self._create_payment_session(user, quantity=1)
        self._pay_created_session(payment_data, payment_id='pay_admc_1')
        order = Order.objects.get(id=payment_data['order_id'])
        order.status = 'PACKING'
        order.save(update_fields=['status'])

        self.client.force_authenticate(user=self.admin)
        response = self.client.post(f'/api/admin/orders/{order.id}/cancel/', {'reason': 'Admin cancel'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, 'CANCELLED')
        self.assertEqual(order.cancelled_by_id, self.admin.id)
        self.assertEqual(Refund.objects.filter(order=order).count(), 1)

    def test_sales_summary_includes_refunds_total_and_net_sales(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/admin/reports/summary/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sales = response.json()['sales_stats']
        self.assertIn('refunds_total', sales)
        self.assertIn('net_sales', sales)
        self.assertIn('cancelled_orders', sales)



class InventoryAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='invadmin', password='securepass123', is_staff=True, is_superuser=True)
        self.user = User.objects.create_user(username='invcustomer', email='inv@example.com', password='securepass123')
        self.product = Product.objects.create(
            name='Stocked Cake',
            category='Birthday Cakes',
            price=Decimal('500.00'),
            discount=0,
            availability='in_stock',
            status='published',
            is_active=True,
            available_quantity=5,
            reserved_quantity=0,
            sold_quantity=0,
            low_stock_threshold=2,
        )

    def _checkout_payload(self, quantity=1, product=None):
        product = product or self.product
        return {
            'items': [{'id': product.id, 'quantity': quantity}],
            'customer_name': 'Inv Customer',
            'customer_email': 'inv@example.com',
            'customer_mobile': '9999999999',
            'shipping_address': '1 Test Lane',
            'city': 'Pune',
            'state': 'MH',
            'postal_code': '411001',
            'country': 'India',
        }

    def test_cannot_buy_more_than_stock(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/payments/create/', self._checkout_payload(quantity=99), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Only', response.data['detail'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 5)
        self.assertEqual(self.product.reserved_quantity, 0)

    def test_checkout_revalidates_stock(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/orders/checkout/', self._checkout_payload(quantity=6), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cart_validate_endpoint(self):
        response = self.client.post('/api/cart/validate/', {
            'items': [{'id': self.product.id, 'quantity': 3}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['valid'])

        bad = self.client.post('/api/cart/validate/', {
            'items': [{'id': self.product.id, 'quantity': 50}],
        }, format='json')
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(bad.data['valid'])

    def test_payment_success_consumes_once(self):
        from django.conf import settings
        self.client.force_authenticate(user=self.user)
        create = self.client.post('/api/payments/create/', self._checkout_payload(quantity=2), format='json')
        self.assertEqual(create.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 3)
        self.assertEqual(self.product.reserved_quantity, 2)

        order_id = create.data['order_id']
        gateway_order_id = create.data['payment_order_id']
        payment_id = 'pay_consume_once'
        signature = build_razorpay_signature(gateway_order_id, payment_id, settings.RAZORPAY_KEY_SECRET)

        verify1 = self.client.post('/api/payments/verify/', {
            'razorpay_order_id': gateway_order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
        }, format='json')
        self.assertEqual(verify1.status_code, status.HTTP_200_OK)

        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 3)
        self.assertEqual(self.product.reserved_quantity, 0)
        self.assertEqual(self.product.sold_quantity, 2)

        # Second verify (idempotent) must not double-consume.
        verify2 = self.client.post('/api/payments/verify/', {
            'razorpay_order_id': gateway_order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
        }, format='json')
        self.assertEqual(verify2.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.sold_quantity, 2)
        self.assertEqual(
            InventoryTransaction.objects.filter(
                reference_type='order', reference_id=str(order_id), adjustment_type='ORDER_CONSUMED'
            ).count(),
            1,
        )

    def test_unpaid_cancel_releases_stock(self):
        self.client.force_authenticate(user=self.user)
        create = self.client.post('/api/payments/create/', self._checkout_payload(quantity=2), format='json')
        order_id = create.data['order_id']
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved_quantity, 2)

        cancel = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'changed mind'}, format='json')
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 5)
        self.assertEqual(self.product.reserved_quantity, 0)

        # Double cancel should not double-release.
        cancel2 = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'again'}, format='json')
        self.assertIn(cancel2.status_code, (status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT))
        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 5)

    def test_paid_cancel_restores_once(self):
        from django.conf import settings
        self.client.force_authenticate(user=self.user)
        create = self.client.post('/api/payments/create/', self._checkout_payload(quantity=1), format='json')
        order_id = create.data['order_id']
        gateway_order_id = create.data['payment_order_id']
        payment_id = 'pay_restore_once'
        signature = build_razorpay_signature(gateway_order_id, payment_id, settings.RAZORPAY_KEY_SECRET)
        self.client.post('/api/payments/verify/', {
            'razorpay_order_id': gateway_order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
        }, format='json')
        self.product.refresh_from_db()
        self.assertEqual(self.product.sold_quantity, 1)
        self.assertEqual(self.product.available_quantity, 4)

        cancel = self.client.post(f'/api/orders/{order_id}/cancel/', {'reason': 'admin style'}, format='json')
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 5)
        self.assertEqual(self.product.sold_quantity, 0)
        self.assertEqual(
            InventoryTransaction.objects.filter(
                reference_type='order', reference_id=str(order_id), adjustment_type='ORDER_RESTORED'
            ).count(),
            1,
        )

    def test_admin_adjust_restock_history_and_non_admin_forbidden(self):
        # Non-admin
        self.client.force_authenticate(user=self.user)
        denied = self.client.get('/api/admin/inventory/')
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.admin)
        listed = self.client.get('/api/admin/inventory/')
        self.assertEqual(listed.status_code, status.HTTP_200_OK)

        adjust = self.client.post(f'/api/admin/inventory/{self.product.id}/adjust/', {
            'action': 'restock',
            'quantity': 10,
            'reason': 'Weekly bake',
        }, format='json')
        self.assertEqual(adjust.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 15)

        history = self.client.get(f'/api/admin/inventory/{self.product.id}/history/')
        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(history.data['count'], 1)

        low = self.client.get('/api/admin/inventory/low-stock/')
        self.assertEqual(low.status_code, status.HTTP_200_OK)

    def test_availability_sync_low_and_out(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(f'/api/admin/inventory/{self.product.id}/adjust/', {
            'action': 'set',
            'quantity': 2,
            'reason': 'force low',
            'low_stock_threshold': 2,
        }, format='json')
        self.product.refresh_from_db()
        self.assertEqual(self.product.availability, 'low_stock')

        self.client.post(f'/api/admin/inventory/{self.product.id}/adjust/', {
            'action': 'set',
            'quantity': 0,
            'reason': 'sold out',
        }, format='json')
        self.product.refresh_from_db()
        self.assertEqual(self.product.availability, 'out_of_stock')

    def test_product_list_exposes_stock_remaining_hides_reserved(self):
        response = self.client.get('/api/catalog/products/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if isinstance(response.data, dict) and 'results' in response.data else response.data
        row = next(p for p in results if p['id'] == self.product.id)
        self.assertIn('available_quantity', row)
        self.assertIn('stock_remaining', row)
        self.assertNotIn('reserved_quantity', row)
        self.assertNotIn('sold_quantity', row)

    def test_report_summary_includes_inventory_counts(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/admin/reports/summary/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        stats = response.data.get('product_stats') or response.data
        # summary nests under product_stats
        product_stats = response.data.get('product_stats', response.data)
        self.assertIn('in_stock_products', product_stats)
        self.assertIn('low_stock_products', product_stats)
        self.assertIn('out_of_stock_products', product_stats)



class CategoryCatalogApiTests(TestCase):
    def setUp(self):
        self.birthday = Product.objects.create(
            name='API Birthday Cake',
            category='Birthday Cakes',
            price=Decimal('999.00'),
            status='published',
            is_active=True,
            main_image='https://images.unsplash.com/photo-1563729784474-d77dbb933a9e?auto=format&fit=crop&w=1000&q=85',
        )
        self.chocolate = Product.objects.create(
            name='API Chocolate Cake',
            category='Chocolate Cakes',
            price=Decimal('1199.00'),
            status='published',
            is_active=True,
            main_image='https://images.unsplash.com/photo-1578985545062-69928b1d9587?auto=format&fit=crop&w=1000&q=85',
        )
        Product.objects.create(
            name='Draft Hidden Cake',
            category='Wedding Cakes',
            price=Decimal('2000.00'),
            status='draft',
            is_active=True,
        )

    def test_categories_lists_only_published_with_counts(self):
        response = self.client.get('/api/catalog/categories/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        by_name = {row['name']: row for row in data}
        self.assertIn('Birthday Cakes', by_name)
        self.assertIn('Chocolate Cakes', by_name)
        self.assertNotIn('Wedding Cakes', by_name)  # draft only
        self.assertGreaterEqual(by_name['Birthday Cakes']['product_count'], 1)
        self.assertTrue(by_name['Birthday Cakes']['image'])

    def test_product_list_filters_by_category(self):
        response = self.client.get('/api/catalog/products/', {'category': 'Chocolate Cakes'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(len(data) >= 1)
        self.assertTrue(all(p['category'].lower() == 'chocolate cakes' for p in data))
        names = {p['name'] for p in data}
        self.assertIn('API Chocolate Cake', names)
        self.assertNotIn('API Birthday Cake', names)

    def test_product_list_all_cakes_ignores_category_sentinel(self):
        response = self.client.get('/api/catalog/products/', {'category': 'All Cakes'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        cats = {p['category'] for p in data}
        self.assertIn('Birthday Cakes', cats)
        self.assertIn('Chocolate Cakes', cats)


class InventoryConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name='Last Unit Cake',
            category='Birthday Cakes',
            price=Decimal('100.00'),
            availability='in_stock',
            status='published',
            is_active=True,
            available_quantity=1,
            reserved_quantity=0,
            sold_quantity=0,
            low_stock_threshold=1,
        )
        self.user = User.objects.create_user(username='raceuser', password='securepass123')

    def test_two_reserves_for_last_unit_only_one_succeeds(self):
        from django.db import transaction
        order_a = Order.objects.create(
            user=self.user,
            order_number='PB-RACE-A',
            customer_name='A',
            customer_email='a@example.com',
            customer_mobile='1',
            shipping_address='x',
            city='c',
            state='s',
            postal_code='1',
            country='IN',
            total_amount=Decimal('100'),
            status='PENDING',
            payment_status='pending',
        )
        order_b = Order.objects.create(
            user=self.user,
            order_number='PB-RACE-B',
            customer_name='B',
            customer_email='b@example.com',
            customer_mobile='1',
            shipping_address='x',
            city='c',
            state='s',
            postal_code='1',
            country='IN',
            total_amount=Decimal('100'),
            status='PENDING',
            payment_status='pending',
        )
        OrderItem.objects.create(
            order=order_a, product=self.product, product_name=self.product.name,
            unit_price=Decimal('100'), quantity=1, subtotal=Decimal('100'),
        )
        OrderItem.objects.create(
            order=order_b, product=self.product, product_name=self.product.name,
            unit_price=Decimal('100'), quantity=1, subtotal=Decimal('100'),
        )

        results = []

        def try_reserve(order):
            try:
                with transaction.atomic():
                    inventory_service.reserve_order(order, user=self.user)
                results.append('ok')
            except Exception:
                results.append('fail')

        try_reserve(order_a)
        try_reserve(order_b)
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(results.count('fail'), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.available_quantity, 0)
        self.assertEqual(self.product.reserved_quantity, 1)




class CouponAPITests(TestCase):
    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta
        from .models import Coupon

        self.timezone = timezone
        self.timedelta = timedelta
        self.Coupon = Coupon
        self.client = APIClient()
        self.admin = User.objects.create_user(username='coupon_admin', password='securepass123', is_staff=True)
        self.user = User.objects.create_user(username='coupon_user', email='coupon@example.com', password='securepass123')
        self.product = Product.objects.create(
            name='Coupon Cake',
            category='Birthday Cakes',
            price=Decimal('1000.00'),
            discount=0,
            availability='in_stock',
            status='published',
            is_active=True,
            available_quantity=100,
        )
        self.discounted_product = Product.objects.create(
            name='Already Discounted Cake',
            category='Chocolate Cakes',
            price=Decimal('1000.00'),
            discount=20,
            availability='in_stock',
            status='published',
            is_active=True,
            available_quantity=100,
        )

    def _make_coupon(self, **kwargs):
        defaults = dict(
            code='SAVE10',
            name='Save 10',
            discount_type='percentage',
            discount_value=Decimal('10'),
            minimum_order_amount=Decimal('0'),
            is_active=True,
            applies_to='all',
        )
        defaults.update(kwargs)
        return self.Coupon.objects.create(**defaults)

    def _checkout_fields(self):
        return {
            'customer_name': 'Coupon User',
            'customer_email': 'coupon@example.com',
            'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
        }

    def test_validate_valid_percentage(self):
        self._make_coupon()
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'save10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertTrue(data['valid'])
        self.assertEqual(data['code'], 'SAVE10')
        self.assertEqual(float(data['discount_amount']), 100.0)

    def test_validate_invalid_code(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'NOPE',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.json()['valid'])

    def test_validate_inactive(self):
        self._make_coupon(is_active=False)
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validate_expired(self):
        past = self.timezone.now() - self.timedelta(days=2)
        self._make_coupon(start_at=past - self.timedelta(days=5), end_at=past)
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validate_future(self):
        future = self.timezone.now() + self.timedelta(days=2)
        self._make_coupon(start_at=future, end_at=future + self.timedelta(days=5))
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validate_min_order(self):
        self._make_coupon(minimum_order_amount=Decimal('2000'))
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validate_max_discount_cap(self):
        self._make_coupon(discount_type='percentage', discount_value=Decimal('50'), maximum_discount_amount=Decimal('100'))
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.json()['discount_amount']), 100.0)

    def test_validate_fixed_amount(self):
        self._make_coupon(discount_type='fixed_amount', discount_value=Decimal('150'))
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.json()['discount_amount']), 150.0)

    def test_validate_product_specific(self):
        coupon = self._make_coupon(applies_to='products')
        coupon.products.add(self.product)
        self.client.force_authenticate(user=self.user)
        ok = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        bad = self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.discounted_product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validate_does_not_increment_usage(self):
        coupon = self._make_coupon(usage_limit=5)
        self.client.force_authenticate(user=self.user)
        self.client.post('/api/coupons/validate/', {
            'code': 'SAVE10',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        coupon.refresh_from_db()
        self.assertEqual(coupon.total_used, 0)

    def test_payment_create_applies_coupon_to_amount(self):
        self._make_coupon(discount_type='fixed_amount', discount_value=Decimal('100'))
        self.client.force_authenticate(user=self.user)
        payload = {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }
        response = self.client.post('/api/payments/create/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['amount'], 90000)  # 900 INR in paise
        order = Order.objects.get(id=data['order_id'])
        self.assertEqual(order.coupon_code, 'SAVE10')
        self.assertEqual(float(order.discount_amount), 100.0)
        self.assertEqual(float(order.total_amount), 900.0)
        from .models import CouponRedemption, Coupon
        redemption = CouponRedemption.objects.get(order=order)
        self.assertEqual(redemption.status, 'pending')
        self.assertEqual(Coupon.objects.get(code='SAVE10').total_used, 1)

    def test_payment_verify_finalizes_redemption(self):
        self._make_coupon(discount_type='fixed_amount', discount_value=Decimal('100'))
        self.client.force_authenticate(user=self.user)
        create = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }, format='json').json()
        order_id = create['payment_order_id']
        payment_id = 'pay_coupon_1'
        signature = build_razorpay_signature(order_id, payment_id, 'test_razorpay_secret')
        verify = self.client.post('/api/payments/verify/', {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
            'amount': create['amount'],
        }, format='json')
        self.assertEqual(verify.status_code, status.HTTP_200_OK)
        from .models import CouponRedemption
        redemption = CouponRedemption.objects.get(order_id=create['order_id'])
        self.assertEqual(redemption.status, 'redeemed')
        self.assertIsNotNone(redemption.redeemed_at)

    def test_checkout_revalidate_rejects_bad_coupon_after_cart_change(self):
        self._make_coupon(minimum_order_amount=Decimal('1500'))
        self.client.force_authenticate(user=self.user)
        # Validate would fail for qty=1; payment create must also reject.
        response = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_global_usage_limit(self):
        self._make_coupon(usage_limit=1, discount_type='fixed_amount', discount_value=Decimal('50'))
        self.client.force_authenticate(user=self.user)
        first = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        other = User.objects.create_user(username='other_coupon', email='o@example.com', password='securepass123')
        self.client.force_authenticate(user=other)
        second = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **{**self._checkout_fields(), 'customer_email': 'o@example.com'},
        }, format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_per_user_limit(self):
        self._make_coupon(usage_limit_per_user=1, discount_type='fixed_amount', discount_value=Decimal('50'))
        self.client.force_authenticate(user=self.user)
        first = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }, format='json')
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cancel_unpaid_voids_but_does_not_restore_per_user_by_default(self):
        from django.conf import settings
        self.assertFalse(getattr(settings, 'COUPON_RESTORE_ON_CANCEL', True))
        self._make_coupon(usage_limit_per_user=1, usage_limit=10, discount_type='fixed_amount', discount_value=Decimal('50'))
        self.client.force_authenticate(user=self.user)
        create = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }, format='json').json()
        from .models import Coupon, CouponRedemption
        coupon = Coupon.objects.get(code='SAVE10')
        self.assertEqual(coupon.total_used, 1)
        cancel = self.client.post(f"/api/orders/{create['order_id']}/cancel/", {'reason': 'changed mind'}, format='json')
        self.assertEqual(cancel.status_code, status.HTTP_200_OK)
        coupon.refresh_from_db()
        self.assertEqual(coupon.total_used, 0)  # global released
        redemption = CouponRedemption.objects.get(order_id=create['order_id'])
        self.assertEqual(redemption.status, 'voided')
        # Per-user still blocked
        again = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE10',
            **self._checkout_fields(),
        }, format='json')
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_customer_forbidden_on_admin_coupon_apis(self):
        self._make_coupon()
        self.client.force_authenticate(user=self.user)
        list_resp = self.client.get('/api/admin/coupons/')
        self.assertEqual(list_resp.status_code, status.HTTP_403_FORBIDDEN)
        create_resp = self.client.post('/api/admin/coupons/', {
            'code': 'HACK',
            'discount_type': 'percentage',
            'discount_value': 10,
        }, format='json')
        self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_coupon_crud(self):
        self.client.force_authenticate(user=self.admin)
        create = self.client.post('/api/admin/coupons/', {
            'code': 'admin20',
            'name': 'Admin 20',
            'discount_type': 'percentage',
            'discount_value': '20',
            'is_active': True,
            'applies_to': 'all',
        }, format='json')
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)
        coupon_id = create.json()['id']
        self.assertEqual(create.json()['code'], 'ADMIN20')
        patch = self.client.patch(f'/api/admin/coupons/{coupon_id}/', {'is_active': False}, format='json')
        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        self.assertFalse(patch.json()['is_active'])
        listing = self.client.get('/api/admin/coupons/')
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(listing.json()['count'], 1)

    def test_admin_rejects_negative_and_bad_pct(self):
        self.client.force_authenticate(user=self.admin)
        bad_pct = self.client.post('/api/admin/coupons/', {
            'code': 'BADPCT',
            'discount_type': 'percentage',
            'discount_value': '150',
        }, format='json')
        self.assertEqual(bad_pct.status_code, status.HTTP_400_BAD_REQUEST)
        bad_neg = self.client.post('/api/admin/coupons/', {
            'code': 'BADNEG',
            'discount_type': 'fixed_amount',
            'discount_value': '-5',
        }, format='json')
        self.assertEqual(bad_neg.status_code, status.HTTP_400_BAD_REQUEST)


class CouponConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from .models import Coupon
        self.Coupon = Coupon
        self.product = Product.objects.create(
            name='Concurrent Cake',
            category='Birthday Cakes',
            price=Decimal('500.00'),
            discount=0,
            availability='in_stock',
            status='published',
            is_active=True,
            available_quantity=50,
        )
        self.Coupon.objects.create(
            code='ONCE',
            discount_type='fixed_amount',
            discount_value=Decimal('50'),
            usage_limit=1,
            is_active=True,
            applies_to='all',
        )

    def test_concurrent_global_limit(self):
        """
        SQLite cannot reliably run threaded HTTP against one DB file (table locked).
        Assert the atomic conditional total_used increment used by reserve_coupon_for_order
        only allows usage_limit successes.
        """
        from django.db import transaction
        from django.db.models import F
        from .models import Coupon, CouponRedemption, Order
        from . import coupons as coupon_service

        coupon = Coupon.objects.get(code='ONCE')
        users = [
            User.objects.create_user(username=f'cuser{i}', email=f'c{i}@example.com', password='securepass123')
            for i in range(2)
        ]
        successes = 0
        failures = 0
        for i, user in enumerate(users):
            order = Order.objects.create(
                user=user,
                order_number=f'PB-CONCUR-{i}-{user.id}',
                customer_name=user.username,
                customer_email=user.email,
                customer_mobile='9876543210',
                shipping_address='12 Market Road',
                city='Mumbai',
                state='Maharashtra',
                postal_code='400001',
                country='India',
                subtotal_amount=Decimal('500.00'),
                total_amount=Decimal('500.00'),
                status='PENDING',
                payment_status='pending',
            )
            cart_rows = [{
                'product': self.product,
                'quantity': 1,
                'unit_price': self.product.discounted_price,
            }]
            try:
                with transaction.atomic():
                    coupon_service.reserve_coupon_for_order(user, 'ONCE', cart_rows, order)
                successes += 1
            except Exception:
                failures += 1

        coupon.refresh_from_db()
        self.assertEqual(successes, 1)
        self.assertEqual(failures, 1)
        self.assertEqual(coupon.total_used, 1)
        self.assertEqual(CouponRedemption.objects.filter(coupon=coupon, status='pending').count(), 1)



class AddressDeliveryAPITests(TestCase):
    def setUp(self):
        from accounts.models import CustomerAddress
        from .models import DeliveryZone, DeliverySettings, Coupon

        self.CustomerAddress = CustomerAddress
        self.DeliveryZone = DeliveryZone
        self.DeliverySettings = DeliverySettings
        self.Coupon = Coupon
        self.client = APIClient()
        self.user = User.objects.create_user(username='addr_user', email='addr@example.com', password='securepass123')
        self.other = User.objects.create_user(username='addr_other', email='other@example.com', password='securepass123')
        self.admin = User.objects.create_user(username='addr_admin', password='securepass123', is_staff=True)
        self.product = Product.objects.create(
            name='Delivery Cake',
            category='Birthday Cakes',
            price=Decimal('500.00'),
            discount=0,
            availability='in_stock',
            status='published',
            is_active=True,
            available_quantity=50,
        )
        self.settings = DeliverySettings.get_solo()
        self.settings.delivery_enabled = True
        self.settings.default_delivery_charge = Decimal('0.00')
        self.settings.bakery_latitude = Decimal('19.076000')
        self.settings.bakery_longitude = Decimal('72.877700')
        self.settings.max_delivery_radius_km = Decimal('15.00')
        self.settings.per_km_charge = None
        self.settings.free_delivery_threshold = None
        self.settings.save()
        self.zone = DeliveryZone.objects.create(
            name='South Mumbai',
            postal_codes=['400001', '400002'],
            is_active=True,
            delivery_charge=Decimal('50.00'),
            minimum_order_amount=Decimal('0.00'),
            free_delivery_threshold=Decimal('1000.00'),
            eta_min_minutes=30,
            eta_max_minutes=60,
        )

    def _addr_payload(self, **kwargs):
        data = {
            'full_name': 'Addr User',
            'mobile_number': '9876543210',
            'address_line_1': '12 Market Road',
            'address_line_2': 'Near Station',
            'landmark': 'Scout Camp',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
            'latitude': '19.080000',
            'longitude': '72.880000',
            'address_type': 'HOME',
            'is_default': True,
        }
        data.update(kwargs)
        return data

    def _checkout_fields(self, **kwargs):
        data = {
            'customer_name': 'Addr User',
            'customer_email': 'addr@example.com',
            'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road',
            'city': 'Mumbai',
            'state': 'Maharashtra',
            'postal_code': '400001',
            'country': 'India',
            'shipping_latitude': '19.080000',
            'shipping_longitude': '72.880000',
        }
        data.update(kwargs)
        return data

    def test_address_crud_and_default_swap(self):
        self.client.force_authenticate(user=self.user)
        r1 = self.client.post('/api/addresses/', self._addr_payload(), format='json')
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED)
        a1 = r1.json()
        self.assertTrue(a1['is_default'])
        self.assertEqual(a1['postal_code'], '400001')

        r2 = self.client.post('/api/addresses/', self._addr_payload(
            full_name='Work Addr', postal_code='400002', is_default=True, address_type='WORK',
        ), format='json')
        self.assertEqual(r2.status_code, status.HTTP_201_CREATED)
        a2 = r2.json()
        self.assertTrue(a2['is_default'])
        a1_refreshed = self.client.get(f"/api/addresses/{a1['id']}/").json()
        self.assertFalse(a1_refreshed['is_default'])

        set_def = self.client.post(f"/api/addresses/{a1['id']}/set-default/")
        self.assertEqual(set_def.status_code, status.HTTP_200_OK)
        self.assertTrue(set_def.json()['is_default'])

        listing = self.client.get('/api/addresses/')
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.json()['count'], 2)

        patched = self.client.patch(f"/api/addresses/{a1['id']}/", {'landmark': 'Updated'}, format='json')
        self.assertEqual(patched.status_code, status.HTTP_200_OK)
        self.assertEqual(patched.json()['landmark'], 'Updated')

        deleted = self.client.delete(f"/api/addresses/{a2['id']}/")
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)

    def test_address_idor_forbidden(self):
        self.client.force_authenticate(user=self.user)
        created = self.client.post('/api/addresses/', self._addr_payload(), format='json').json()
        self.client.force_authenticate(user=self.other)
        resp = self.client.get(f"/api/addresses/{created['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self.client.patch(f"/api/addresses/{created['id']}/", {'city': 'Pune'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self.client.delete(f"/api/addresses/{created['id']}/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_postal_and_mobile(self):
        self.client.force_authenticate(user=self.user)
        bad_pin = self.client.post('/api/addresses/', self._addr_payload(postal_code='123'), format='json')
        self.assertEqual(bad_pin.status_code, status.HTTP_400_BAD_REQUEST)
        bad_mobile = self.client.post('/api/addresses/', self._addr_payload(mobile_number='12345'), format='json')
        self.assertEqual(bad_mobile.status_code, status.HTTP_400_BAD_REQUEST)

    def test_supported_and_unsupported_pin(self):
        self.client.force_authenticate(user=self.user)
        ok = self.client.post('/api/delivery/quote/', {
            'postal_code': '400001',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(ok.status_code, status.HTTP_200_OK)
        self.assertTrue(ok.json()['eligible'])
        self.assertEqual(ok.json()['delivery_fee'], 50.0)

        bad = self.client.post('/api/delivery/quote/', {
            'postal_code': '999999',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(bad.status_code, status.HTTP_200_OK)
        self.assertFalse(bad.json()['eligible'])

    def test_min_order_and_free_delivery(self):
        self.zone.minimum_order_amount = Decimal('600.00')
        self.zone.save()
        self.client.force_authenticate(user=self.user)
        # product 500 < 600
        low = self.client.post('/api/delivery/quote/', {
            'postal_code': '400001',
            'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertFalse(low.json()['eligible'])
        self.assertFalse(low.json()['min_order_ok'])

        self.zone.minimum_order_amount = Decimal('0')
        self.zone.save()
        # 2 x 500 = 1000 >= free threshold
        free = self.client.post('/api/delivery/quote/', {
            'postal_code': '400001',
            'items': [{'id': self.product.id, 'quantity': 2}],
        }, format='json')
        self.assertTrue(free.json()['eligible'])
        self.assertEqual(free.json()['delivery_fee'], 0.0)
        self.assertTrue(free.json()['free_delivery_applied'])

    def test_payment_includes_delivery_and_coupon_merchandise_only(self):
        coupon = self.Coupon.objects.create(
            code='SAVE50',
            name='Save 50',
            discount_type='fixed',
            discount_value=Decimal('50'),
            is_active=True,
            applies_to='all',
        )
        self.client.force_authenticate(user=self.user)
        # subtotal 500, coupon 50 -> 450, delivery 50 -> total 500
        resp = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'coupon_code': 'SAVE50',
            **self._checkout_fields(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        # amount is paise
        self.assertEqual(resp.json()['amount'], 50000)
        self.assertEqual(resp.json()['delivery_fee'], 50.0)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertEqual(order.delivery_fee, Decimal('50.00'))
        self.assertEqual(order.discount_amount, Decimal('50.00'))
        self.assertEqual(order.total_amount, Decimal('500.00'))
        self.assertEqual(order.delivery_zone_id, self.zone.id)

    def test_payment_with_address_id_snapshots_and_survives_delete(self):
        self.client.force_authenticate(user=self.user)
        addr = self.client.post('/api/addresses/', self._addr_payload(), format='json').json()
        resp = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'address_id': addr['id'],
            'customer_email': 'addr@example.com',
            'customer_name': 'Addr User',
            'customer_mobile': '9876543210',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertEqual(order.shipping_address, '12 Market Road')
        self.assertEqual(order.postal_code, '400001')
        self.assertEqual(order.landmark, 'Scout Camp')
        self.assertIsNotNone(order.shipping_latitude)
        self.assertEqual(order.address_id, addr['id'])
        self.client.delete(f"/api/addresses/{addr['id']}/")
        order.refresh_from_db()
        self.assertIsNone(order.address_id)
        self.assertEqual(order.shipping_address, '12 Market Road')
        self.assertEqual(float(order.delivery_fee), 50.0)

    def test_zone_disabled_rejects(self):
        self.zone.is_active = False
        self.zone.save()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post('/api/payments/create/', {
            'items': [{'id': self.product.id, 'quantity': 1}],
            **self._checkout_fields(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_radius_rejection(self):
        self.client.force_authenticate(user=self.user)
        # Far coords ~100+ km from bakery
        resp = self.client.post('/api/delivery/quote/', {
            'postal_code': '400001',
            'items': [{'id': self.product.id, 'quantity': 1}],
            'shipping_latitude': '28.613900',
            'shipping_longitude': '77.209000',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.json()['eligible'])
        self.assertIn('radius', resp.json()['message'].lower())

    def test_customer_forbidden_on_admin_zones(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get('/api/admin/delivery-zones/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        resp = self.client.get('/api/admin/delivery-settings/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_zone_crud_and_settings(self):
        self.client.force_authenticate(user=self.admin)
        created = self.client.post('/api/admin/delivery-zones/', {
            'name': 'Andheri',
            'postal_codes': ['400053', '400058'],
            'delivery_charge': '40.00',
            'minimum_order_amount': '100.00',
            'is_active': True,
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        zone_id = created.json()['id']
        patched = self.client.patch(f'/api/admin/delivery-zones/{zone_id}/', {'is_active': False}, format='json')
        self.assertEqual(patched.status_code, status.HTTP_200_OK)
        self.assertFalse(patched.json()['is_active'])

        settings_get = self.client.get('/api/admin/delivery-settings/')
        self.assertEqual(settings_get.status_code, status.HTTP_200_OK)
        settings_patch = self.client.patch('/api/admin/delivery-settings/', {
            'per_km_charge': '5.00',
            'max_delivery_radius_km': '20.00',
        }, format='json')
        self.assertEqual(settings_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(float(settings_patch.json()['per_km_charge']), 5.0)



class AdminOpsControlCenterTests(TestCase):
    """Authz + dashboard + status transitions + customers + settings for admin control center."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='ops_admin', password='securepass123', is_staff=True, is_superuser=True,
        )
        self.customer = User.objects.create_user(
            username='ops_customer', email='ops_customer@example.com', password='securepass123',
        )
        self.delivery_user = User.objects.create_user(
            username='ops_delivery', email='ops_delivery@example.com', password='securepass123', is_staff=False,
        )
        delivery_category, _ = EmployeeCategory.objects.get_or_create(name='Delivery Staff')
        self.employee = Employee.objects.create(
            user=self.delivery_user,
            category=delivery_category,
            employee_id='EMP-OPS-1',
            name='Ops Rider',
            contact_number='9000000001',
            status='AVAILABLE',
        )
        self.product = Product.objects.create(
            name='Ops Cake', price=500, discount=0, available_quantity=20, category='Birthday Cakes',
        )

    def _make_order(self, user=None, status='ORDER_CONFIRMED', payment_status='paid'):
        user = user or self.customer
        return Order.objects.create(
            user=user,
            order_number=f'OPS-{Order.objects.count()+1:04d}',
            customer_name=user.username,
            customer_email=user.email or f'{user.username}@example.com',
            customer_mobile='9876543210',
            shipping_address='1 Test St',
            city='Kolkata',
            state='WB',
            postal_code='700001',
            country='India',
            subtotal_amount=500,
            total_amount=500,
            status=status,
            payment_status=payment_status,
        )

    def test_customer_blocked_from_admin_dashboard(self):
        self.client.force_authenticate(user=self.customer)
        for url in [
            '/api/admin/dashboard/',
            '/api/admin/customers/',
            '/api/admin/settings/status/',
            '/api/admin/orders/',
            '/api/admin/payments/',
            '/api/admin/refunds/',
            '/api/admin/deliveries/active/',
            '/api/admin/exports/orders/',
        ]:
            resp = self.client.get(url)
            self.assertIn(resp.status_code, (401, 403), msg=f'{url} -> {resp.status_code}')

    def test_delivery_employee_blocked_from_unrestricted_admin(self):
        self.client.force_authenticate(user=self.delivery_user)
        for url in [
            '/api/admin/dashboard/',
            '/api/admin/customers/',
            '/api/admin/orders/',
            '/api/admin/inventory/',
            '/api/admin/coupons/',
            '/api/admin/reviews/',
        ]:
            resp = self.client.get(url)
            self.assertIn(resp.status_code, (401, 403), msg=f'{url} -> {resp.status_code}')

    def test_unauthenticated_rejected(self):
        resp = self.client.get('/api/admin/dashboard/')
        self.assertIn(resp.status_code, (401, 403))
        resp = self.client.get('/api/admin/customers/')
        self.assertIn(resp.status_code, (401, 403))

    def test_admin_allowed_dashboard_and_metrics(self):
        self.client.force_authenticate(user=self.admin)
        self._make_order(status='PREPARING')
        self._make_order(status='OUT_FOR_DELIVERY')
        resp = self.client.get('/api/admin/dashboard/?preset=today')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('orders', data)
        self.assertIn('payments', data)
        self.assertIn('products', data)
        self.assertIn('customers', data)
        self.assertIn('reviews', data)
        self.assertIn('coupons', data)
        self.assertIn('delivery', data)
        self.assertIn('sales_summary', data)
        self.assertIn('gross_sales', data['sales_summary'])
        self.assertIn('net_sales', data['sales_summary'])
        self.assertGreaterEqual(data['orders']['preparing'], 1)
        self.assertGreaterEqual(data['orders']['out_for_delivery'], 1)

    def test_order_status_valid_and_invalid_transition(self):
        order = self._make_order(status='ORDER_CONFIRMED')
        self.client.force_authenticate(user=self.admin)
        bad = self.client.patch(f'/api/admin/orders/{order.id}/status/', {'status': 'DELIVERED'}, format='json')
        self.assertEqual(bad.status_code, 400)
        good = self.client.patch(f'/api/admin/orders/{order.id}/status/', {'status': 'PREPARING'}, format='json')
        self.assertEqual(good.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'PREPARING')
        self.assertTrue(AdminActivity.objects.filter(action='order_status', entity_id=order.id).exists())
        # Cancel via status update rejected
        cancel_via_status = self.client.patch(
            f'/api/admin/orders/{order.id}/status/', {'status': 'CANCELLED'}, format='json',
        )
        self.assertEqual(cancel_via_status.status_code, 400)

    def test_orders_search_filter_pagination(self):
        self._make_order(status='ORDER_CONFIRMED')
        self._make_order(status='PREPARING')
        self.client.force_authenticate(user=self.admin)
        listed = self.client.get('/api/admin/orders/?status=PREPARING&page=1&page_size=10')
        self.assertEqual(listed.status_code, 200)
        body = listed.json()
        self.assertIn('results', body)
        self.assertIn('page', body)
        self.assertTrue(all(r['status'] == 'PREPARING' for r in body['results']))
        search = self.client.get('/api/admin/orders/?search=ops_customer')
        self.assertEqual(search.status_code, 200)
        self.assertGreaterEqual(search.json()['count'], 1)
        detail = self.client.get(f'/api/admin/orders/{Order.objects.first().id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('payments', detail.json())
        self.assertIn('status_history', detail.json())

    def test_customers_search_and_activate(self):
        self.client.force_authenticate(user=self.admin)
        listed = self.client.get('/api/admin/customers/?search=ops_customer')
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(listed.json()['count'], 1)
        detail = self.client.get(f'/api/admin/customers/{self.customer.id}/')
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn('password', detail.json())
        deactivate = self.client.patch(
            f'/api/admin/customers/{self.customer.id}/', {'is_active': False}, format='json',
        )
        self.assertEqual(deactivate.status_code, 200)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_active)
        elevate = self.client.patch(
            f'/api/admin/customers/{self.customer.id}/', {'is_staff': True}, format='json',
        )
        self.assertEqual(elevate.status_code, 400)
        self.customer.refresh_from_db()
        self.assertFalse(self.customer.is_staff)

    def test_settings_status_never_exposes_secrets(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get('/api/admin/settings/status/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        dumped = str(body).lower()
        self.assertIn('integrations', body)
        self.assertIn('smtp_configured', body['integrations'])
        self.assertIn('sms_configured', body['integrations'])
        self.assertIn('whatsapp_configured', body['integrations'])
        self.assertIn('payment_configured', body['integrations'])
        for banned in ['password', 'secret', 'auth_token', 'webhook_secret', 'twilio_auth_token']:
            self.assertNotIn(banned, dumped)
        # Reject secret writes
        bad = self.client.patch('/api/admin/settings/status/', {'EMAIL_HOST_PASSWORD': 'x'}, format='json')
        self.assertEqual(bad.status_code, 400)
        # Allow bakery location update
        ok = self.client.patch(
            '/api/admin/settings/status/',
            {'bakery_latitude': '22.572600', 'bakery_longitude': '88.363900'},
            format='json',
        )
        self.assertEqual(ok.status_code, 200)

    def test_reviews_approve_reject_audit_and_public_visibility(self):
        review = Review.objects.create(
            product=self.product, user=self.customer, name='Ops', rating=5,
            comment='Nice', status='pending',
        )
        self.client.force_authenticate(user=self.admin)
        approve = self.client.post(f'/api/admin/reviews/{review.id}/approve/', {}, format='json')
        self.assertEqual(approve.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(review.status, 'approved')
        self.assertTrue(AdminActivity.objects.filter(action='review_moderate', entity_id=review.id).exists())
        public = self.client.get(f'/api/catalog/products/{self.product.id}/reviews/')
        # public endpoint allow any
        self.client.force_authenticate(user=None)
        public = self.client.get(f'/api/catalog/products/{self.product.id}/reviews/')
        self.assertEqual(public.status_code, 200)
        ids = [r['id'] for r in (public.json() if isinstance(public.json(), list) else public.json().get('results', public.json()))]
        # response shape may be list or dict
        payload = public.json()
        if isinstance(payload, dict):
            items = payload.get('results') or payload.get('reviews') or []
        else:
            items = payload
        self.assertTrue(any(r.get('id') == review.id for r in items))

    def test_export_orders_csv_staff_only(self):
        self._make_order()
        self.client.force_authenticate(user=self.customer)
        denied = self.client.get('/api/admin/exports/orders/')
        self.assertIn(denied.status_code, (401, 403))
        self.client.force_authenticate(user=self.admin)
        ok = self.client.get('/api/admin/exports/orders/')
        self.assertEqual(ok.status_code, 200)
        self.assertIn('text/csv', ok['Content-Type'])

    def test_active_deliveries_and_unassign(self):
        order = self._make_order(status='READY_FOR_DELIVERY')
        self.client.force_authenticate(user=self.admin)
        assign = self.client.post(
            f'/api/admin/orders/{order.id}/assign-delivery/',
            {'employee_id': self.employee.id},
            format='json',
        )
        self.assertEqual(assign.status_code, 200)
        active = self.client.get('/api/admin/deliveries/active/')
        self.assertEqual(active.status_code, 200)
        self.assertGreaterEqual(active.json()['count'], 1)
        unassign = self.client.post(f'/api/admin/orders/{order.id}/unassign-delivery/', {}, format='json')
        self.assertEqual(unassign.status_code, 200)
        order.refresh_from_db()
        self.assertIsNone(order.delivery_employee_id)


class EmployeeManagementAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username='employee_admin', password='securepass123', is_staff=True,
        )
        self.customer = User.objects.create_user(
            username='employee_customer', password='securepass123',
        )
        self.delivery_category, _ = EmployeeCategory.objects.get_or_create(name='Delivery Staff')
        self.chef_category, _ = EmployeeCategory.objects.get_or_create(name='Chef')
        self.employee = Employee.objects.create(
            employee_id='PB-EMP-001',
            name='Asha Baker',
            category=self.chef_category,
            designation='Head Baker',
            contact_number='+91 9876543210',
            email='asha@example.com',
            date_of_joining='2025-01-15',
            address='Test address',
            emergency_contact='9876543211',
            status='AVAILABLE',
        )

    def test_employee_endpoints_require_admin(self):
        for user in (None, self.customer):
            self.client.force_authenticate(user=user)
            for url in (
                '/api/admin/employees/',
                '/api/admin/employees/stats/',
                '/api/admin/employee-categories/',
            ):
                response = self.client.get(url)
                self.assertIn(response.status_code, (401, 403), msg=url)

    def test_employee_list_preserves_array_response_and_supports_filtering(self):
        self.client.force_authenticate(user=self.admin)
        default_response = self.client.get('/api/admin/employees/')
        self.assertEqual(default_response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(default_response.json(), list)
        self.assertEqual(default_response.json()[0]['category_name'], 'Chef')

        filtered = self.client.get('/api/admin/employees/', {
            'search': 'asha@example.com',
            'employment_status': 'ACTIVE',
            'category': self.chef_category.id,
            'date_from': '2025-01-01',
            'date_to': '2025-12-31',
            'page': 1,
            'page_size': 10,
        })
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.json()['count'], 1)
        self.assertEqual(filtered.json()['results'][0]['employee_id'], 'PB-EMP-001')

        invalid_date = self.client.get('/api/admin/employees/', {'date_from': 'not-a-date'})
        self.assertEqual(invalid_date.status_code, status.HTTP_400_BAD_REQUEST)

    def test_employee_create_update_and_soft_deactivate(self):
        self.client.force_authenticate(user=self.admin)
        created = self.client.post('/api/admin/employees/', {
            'employee_id': 'PB-EMP-002',
            'name': 'Ravi Rider',
            'contact_number': '9876543212',
            'category': self.delivery_category.id,
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        self.assertEqual(created.json()['category_name'], 'Delivery Staff')
        employee_id = created.json()['id']

        updated = self.client.patch(f'/api/admin/employees/{employee_id}/', {
            'employment_status': 'ON_LEAVE',
        }, format='json')
        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.content)
        self.assertEqual(updated.json()['employment_status'], 'ON_LEAVE')
        self.assertEqual(updated.json()['status'], 'INACTIVE')

        deleted = self.client.delete(f'/api/admin/employees/{self.employee.id}/')
        self.assertEqual(deleted.status_code, status.HTTP_200_OK)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, 'INACTIVE')
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertTrue(AdminActivity.objects.filter(action='employee_deactivate', entity_id=self.employee.id).exists())

    def test_legacy_employee_create_defaults_to_delivery_category(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post('/api/admin/employees/', {
            'employee_id': 'PB-EMP-LEGACY',
            'name': 'Legacy Client',
            'contact_number': '9876543215',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        self.assertEqual(response.json()['category_name'], 'Delivery Staff')

    def test_customer_order_employee_payload_does_not_include_hr_fields(self):
        order = Order.objects.create(
            user=self.customer,
            order_number='EMPLOYEE-PRIVACY-1',
            customer_name='Customer',
            customer_email='customer@example.com',
            customer_mobile='9876543210',
            shipping_address='Test',
            city='Mumbai',
            state='MH',
            postal_code='400001',
            country='India',
            subtotal_amount=500,
            total_amount=500,
            delivery_employee=self.employee,
        )
        from .serializers import OrderSerializer
        employee_payload = OrderSerializer(order).data['delivery_employee']
        self.assertNotIn('address', employee_payload)
        self.assertNotIn('emergency_contact', employee_payload)
        self.assertNotIn('employment_status', employee_payload)

    def test_employee_cannot_be_assigned_to_inactive_category_and_unique_id_is_validated(self):
        inactive_category = EmployeeCategory.objects.create(name='Seasonal', is_active=False)
        self.client.force_authenticate(user=self.admin)
        inactive_assignment = self.client.post('/api/admin/employees/', {
            'employee_id': 'PB-EMP-003',
            'name': 'Inactive Category',
            'contact_number': '9876543213',
            'category': inactive_category.id,
        }, format='json')
        self.assertEqual(inactive_assignment.status_code, status.HTTP_400_BAD_REQUEST)

        duplicate_id = self.client.post('/api/admin/employees/', {
            'employee_id': self.employee.employee_id,
            'name': 'Duplicate ID',
            'contact_number': '9876543214',
            'category': self.delivery_category.id,
        }, format='json')
        self.assertEqual(duplicate_id.status_code, status.HTTP_400_BAD_REQUEST)

    def test_categories_crud_and_statistics(self):
        self.client.force_authenticate(user=self.admin)
        created = self.client.post('/api/admin/employee-categories/', {'name': 'Quality Assurance'}, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        category_id = created.json()['id']
        self.assertEqual(created.json()['employee_count'], 0)

        duplicate = self.client.post('/api/admin/employee-categories/', {'name': 'chef'}, format='json')
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)

        disabled = self.client.patch(f'/api/admin/employee-categories/{category_id}/', {'is_active': False}, format='json')
        self.assertEqual(disabled.status_code, status.HTTP_200_OK)
        self.assertFalse(disabled.json()['is_active'])

        statistics = self.client.get('/api/admin/employees/stats/')
        self.assertEqual(statistics.status_code, status.HTTP_200_OK)
        self.assertEqual(statistics.json()['total'], 1)
        self.assertEqual(statistics.json()['active'], 1)
        category_counts = {category['name']: category['employee_count'] for category in statistics.json()['by_category']}
        self.assertEqual(category_counts['Chef'], 1)
        self.assertEqual(category_counts['Quality Assurance'], 0)


class CartEdgeCaseTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.product = Product.objects.create(
            name="Cart Edge Cake",
            category="Chocolate Cakes",
            price=Decimal("500.00"),
            is_active=True,
            status="published",
            available_quantity=5,
            reserved_quantity=0,
            sold_quantity=0,
            low_stock_threshold=2,
        )

    def test_empty_cart_validate_rejected(self):
        response = self.client.post("/api/cart/validate/", {"items": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.json().get("valid", True))

    def test_zero_quantity_rejected(self):
        response = self.client.post(
            "/api/cart/validate/",
            {"items": [{"id": self.product.id, "quantity": 0}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_payment_create_rejects_empty_cart(self):
        user = User.objects.create_user(username="emptycart", email="emptycart@example.com", password="Pass12345!")
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/payments/create/",
            {
                "items": [],
                "customer_name": "Empty",
                "customer_email": "emptycart@example.com",
                "customer_mobile": "9876543210",
                "shipping_address": "1 St",
                "city": "Mumbai",
                "state": "MH",
                "postal_code": "400001",
                "country": "India",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CacheFreshnessRegressionTests(TestCase):
    """Ensure catalog caches invalidate on product change; payments never use stale price/stock."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = APIClient()
        self.product = Product.objects.create(
            name="Cache Fresh Cake",
            category="Chocolate Cakes",
            price=Decimal("1000.00"),
            discount=0,
            is_active=True,
            status="published",
            available_quantity=20,
            reserved_quantity=0,
            sold_quantity=0,
            low_stock_threshold=5,
            main_image="https://example.com/cache-cake.jpg",
        )
        self.user = User.objects.create_user(
            username="cachepayer", email="cachepayer@example.com", password="Pass12345!"
        )

    def test_product_list_cache_invalidates_on_price_change(self):
        first = self.client.get("/api/products/")
        self.assertEqual(first.status_code, 200)
        rows = first.json()
        if isinstance(rows, dict):
            rows = rows.get("results", [])
        match = next(r for r in rows if r["id"] == self.product.id)
        self.assertEqual(Decimal(str(match["price"])), Decimal("1000.00"))

        # Warm identical query again (should still be correct)
        warm = self.client.get("/api/products/")
        self.assertEqual(warm.status_code, 200)

        self.product.price = Decimal("1500.00")
        self.product.save(update_fields=["price", "updated_at"])

        after = self.client.get("/api/products/")
        self.assertEqual(after.status_code, 200)
        rows2 = after.json()
        if isinstance(rows2, dict):
            rows2 = rows2.get("results", [])
        match2 = next(r for r in rows2 if r["id"] == self.product.id)
        self.assertEqual(Decimal(str(match2["price"])), Decimal("1500.00"))

    def test_categories_cache_invalidates_on_new_published_product(self):
        first = self.client.get("/api/categories/")
        self.assertEqual(first.status_code, 200)
        names_before = {c.get("name") or c.get("category") for c in first.json()}

        Product.objects.create(
            name="Wedding Cache Cake",
            category="Wedding Cakes",
            price=Decimal("2000.00"),
            is_active=True,
            status="published",
            available_quantity=3,
        )
        after = self.client.get("/api/categories/")
        self.assertEqual(after.status_code, 200)
        names_after = {c.get("name") or c.get("category") for c in after.json()}
        self.assertTrue(
            any("Wedding" in (n or "") for n in names_after),
            f"Expected Wedding Cakes in categories after invalidate; got {names_after}",
        )

    def test_payment_create_uses_live_price_after_change(self):
        """Payment path must not serve stale cached catalog price."""
        self.client.force_authenticate(user=self.user)
        self.product.price = Decimal("1800.00")
        self.product.save(update_fields=["price", "updated_at"])

        # Warm product list cache with old-looking query (price already 1800 in DB)
        self.client.get("/api/products/")

        response = self.client.post(
            "/api/payments/create/",
            {
                "items": [{"id": self.product.id, "quantity": 1}],
                "customer_name": "Cache Payer",
                "customer_email": "cachepayer@example.com",
                "customer_mobile": "9876543210",
                "shipping_address": "12 Market Road",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
                "country": "India",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn("order_id", data)
        order = Order.objects.get(id=data["order_id"])
        self.assertEqual(order.subtotal_amount, Decimal("1800.00"))

    def test_payment_create_rejects_oversell_after_stock_drop(self):
        self.client.force_authenticate(user=self.user)
        self.client.get("/api/products/")  # warm list cache
        self.product.available_quantity = 1
        self.product.save(update_fields=["available_quantity", "updated_at"])

        response = self.client.post(
            "/api/payments/create/",
            {
                "items": [{"id": self.product.id, "quantity": 5}],
                "customer_name": "Cache Payer",
                "customer_email": "cachepayer@example.com",
                "customer_mobile": "9876543210",
                "shipping_address": "12 Market Road",
                "city": "Mumbai",
                "state": "Maharashtra",
                "postal_code": "400001",
                "country": "India",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AdminQaMajorsRegressionTests(TestCase):
    """Regression for admin Payments / Refunds / Reports QA mismatches."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username='qa_admin', password='pass12345', is_staff=True, is_superuser=True,
        )
        self.customer_a = User.objects.create_user(username='cust_a', password='pass12345', email='a@ex.com')
        self.customer_b = User.objects.create_user(username='cust_b', password='pass12345', email='b@ex.com')
        self.customer_c = User.objects.create_user(username='cust_c', password='pass12345', email='c@ex.com')
        # Extra staff should not inflate Reports "Customers"/total_users.
        User.objects.create_user(username='qa_staff2', password='pass12345', is_staff=True)

        self.order_with_payment = Order.objects.create(
            user=self.customer_a,
            order_number='QA-PAY-100',
            customer_name='Cust A',
            customer_email='a@ex.com',
            customer_mobile='9000000001',
            shipping_address='1 St',
            city='Kolkata',
            state='WB',
            postal_code='700001',
            country='India',
            subtotal_amount=100,
            total_amount=100,
            status='ORDER_CONFIRMED',
            payment_status='paid',
        )
        self.payment = Payment.objects.create(
            order=self.order_with_payment,
            user=self.customer_a,
            gateway='razorpay',
            gateway_order_id='order_qa_100',
            gateway_payment_id='pay_qa_100',
            amount=100,
            currency='INR',
            status='refund_pending',
            paid_at=timezone.now(),
        )
        self.refund = Refund.objects.create(
            order=self.order_with_payment,
            payment=self.payment,
            user=self.customer_a,
            gateway='razorpay',
            gateway_refund_id='rfnd_qa_100',
            amount=100,
            currency='INR',
            status='processing',
            initiated_by=self.customer_a,
            initiated_by_type='customer',
        )
        # Paid order with NO Payment row (the Payments empty / revenue undercount case).
        self.orphan_paid = Order.objects.create(
            user=self.customer_b,
            order_number='QA-ORPHAN-1130',
            customer_name='Cust B',
            customer_email='b@ex.com',
            customer_mobile='9000000002',
            shipping_address='2 St',
            city='Mumbai',
            state='MH',
            postal_code='400001',
            country='India',
            subtotal_amount=1080,
            delivery_fee=50,
            total_amount=1130,
            status='OUT_FOR_DELIVERY',
            payment_status='paid',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_admin_payments_includes_orphan_paid_order(self):
        resp = self.client.get('/api/admin/payments/', {'page': 1, 'page_size': 25})
        self.assertEqual(resp.status_code, 200, msg=getattr(resp, 'data', resp.content))
        data = resp.json()
        self.assertGreaterEqual(data['count'], 2)
        order_numbers = {row.get('order_number') for row in data['results']}
        self.assertIn('QA-PAY-100', order_numbers)
        self.assertIn('QA-ORPHAN-1130', order_numbers)
        orphan = next(r for r in data['results'] if r.get('order_number') == 'QA-ORPHAN-1130')
        self.assertEqual(orphan.get('source'), 'order_derived')
        self.assertEqual(float(orphan['amount']), 1130.0)

    def test_admin_refunds_lists_processing_and_dashboard_pending(self):
        refunds = self.client.get('/api/admin/refunds/', {'page': 1, 'page_size': 25})
        self.assertEqual(refunds.status_code, 200, msg=getattr(refunds, 'data', refunds.content))
        rdata = refunds.json()
        self.assertGreaterEqual(rdata['count'], 1)
        statuses = {row['status'] for row in rdata['results']}
        self.assertIn('processing', statuses)
        self.assertTrue(any(row.get('order_number') == 'QA-PAY-100' for row in rdata['results']))

        dash = self.client.get('/api/admin/dashboard/', {'preset': 'last_30_days'})
        self.assertEqual(dash.status_code, 200, msg=getattr(dash, 'data', dash.content))
        self.assertGreaterEqual(dash.json()['payments']['refunds_pending'], 1)

    def test_reports_revenue_and_users_align_with_orders_and_customers(self):
        resp = self.client.get('/api/admin/reports/summary/', {'preset': 'last_30_days'})
        self.assertEqual(resp.status_code, 200, msg=getattr(resp, 'data', resp.content))
        data = resp.json()
        sales = data['sales_stats']
        users = data['user_stats']
        # 100 + 1130 paid order totals
        self.assertEqual(float(sales['total_sales']), 1230.0)
        self.assertGreaterEqual(sales['successful_payments'], 2)
        # Customers page definition: non-staff only (3 customers created here; ignore other DB users via lower bound)
        customers_resp = self.client.get('/api/admin/customers/', {'page': 1, 'page_size': 100})
        self.assertEqual(customers_resp.status_code, 200)
        customer_count = customers_resp.json()['count']
        self.assertEqual(users['total_users'], customer_count)

        self.assertEqual(users['total_users'], User.objects.filter(is_staff=False).count())

    def test_admin_refunds_order_number_and_pending_group(self):
        """Refund list must expose human order_number and honor pending_group filter."""
        resp = self.client.get('/api/admin/refunds/', {'page': 1, 'page_size': 25})
        self.assertEqual(resp.status_code, 200, msg=getattr(resp, 'data', resp.content))
        data = resp.json()
        self.assertIn('pending_statuses', data)
        self.assertEqual(set(data['pending_statuses']), {'requested', 'pending', 'processing'})
        row = next(r for r in data['results'] if r.get('id') == self.refund.id)
        self.assertEqual(row.get('order_number'), 'QA-PAY-100')
        self.assertEqual(row.get('order_id'), self.order_with_payment.id)
        # Raw FK may still be present; order_number must win for admin UI.
        self.assertTrue(row.get('order_number'))

        pending = self.client.get('/api/admin/refunds/', {'status': 'pending_group', 'page': 1, 'page_size': 25})
        self.assertEqual(pending.status_code, 200, msg=getattr(pending, 'data', pending.content))
        pdata = pending.json()
        self.assertGreaterEqual(pdata['count'], 1)
        for r in pdata['results']:
            self.assertIn(r['status'], ('requested', 'pending', 'processing'))
        self.assertTrue(any(r.get('order_number') == 'QA-PAY-100' for r in pdata['results']))

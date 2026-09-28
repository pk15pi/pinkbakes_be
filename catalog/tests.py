import hashlib
import json
import hmac
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import InventoryTransaction, Order, OrderItem, OrderStatusHistory, Payment, Product, Refund, Review
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


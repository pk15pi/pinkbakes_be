import hashlib
import hmac
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import Order, OrderItem, Product, Review


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

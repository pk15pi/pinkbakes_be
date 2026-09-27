from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import Product, Review


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

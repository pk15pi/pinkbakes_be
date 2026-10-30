"""BUG-023: anonymous catalog reads get a higher limit; auth/OTP/login stay strict."""
import importlib
import os
import sys
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from .models import Product

TEST_RATES = {
    'anon': '3/minute',
    'catalog_read': '6/minute',
    'user': '1000/minute',
    'auth': '2/minute',
    'otp': '2/minute',
    'payment': '2/minute',
    'admin_login': '2/minute',
}


@mock.patch.object(SimpleRateThrottle, 'THROTTLE_RATES', TEST_RATES)
class CatalogThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.client = APIClient()
        self.product = Product.objects.create(
            name='Throttle Cake', slug='throttle-cake', category='Chocolate Cakes', price=Decimal('500.00'),
            is_active=True, status='published', availability='in_stock', available_quantity=5,
        )

    def test_anonymous_catalog_reads_use_the_higher_catalog_rate(self):
        urls = [
            '/api/catalog/categories/',
            '/api/catalog/products/',
            f'/api/catalog/products/{self.product.id}/',
            '/api/catalog/products/slug/throttle-cake/',
            f'/api/catalog/products/{self.product.id}/reviews/',
            '/api/catalog/products/',
        ]
        # Six reads: more than the anon limit (3) and all within catalog_read (6).
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK, url)
        self.assertEqual(self.client.get('/api/catalog/products/').status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_catalog_reads_do_not_use_up_the_general_anon_budget(self):
        for _ in range(5):
            self.assertEqual(self.client.get('/api/catalog/products/').status_code, status.HTTP_200_OK)
        # Writes on catalog views still use the stricter anon rate (3/minute here).
        codes = [self.client.post(f'/api/catalog/products/{self.product.id}/reviews/', {'rating': 5}, format='json').status_code
                 for _ in range(4)]
        self.assertNotIn(status.HTTP_429_TOO_MANY_REQUESTS, codes[:3])
        self.assertEqual(codes[3], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_sign_in_stays_strict(self):
        body = {'email': 'nobody@example.com', 'password': 'wrong-password'}
        codes = [self.client.post('/api/accounts/signin/', body, format='json').status_code for _ in range(3)]
        self.assertNotIn(status.HTTP_429_TOO_MANY_REQUESTS, codes[:2])
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_admin_login_stays_strict(self):
        body = {'username': 'nobody', 'password': 'wrong-password'}
        codes = [self.client.post('/api/catalog/admin/login/', body, format='json').status_code for _ in range(3)]
        self.assertEqual(codes[2], status.HTTP_429_TOO_MANY_REQUESTS)


class ThrottleSettingsTests(TestCase):
    def _load_settings(self, env):
        import pinkbakes_backend.settings as settings_module
        base = {'DJANGO_DEBUG': 'true', 'SECURE_SSL_REDIRECT': 'false'}
        try:
            with mock.patch.dict(os.environ, {**base, **env}), mock.patch.object(sys, 'argv', ['manage.py', 'check']):
                return dict(importlib.reload(settings_module).REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'])
        finally:
            importlib.reload(settings_module)

    def test_rates_have_defaults_and_can_be_set_from_env(self):
        with mock.patch.dict(os.environ):
            for key in ('THROTTLE_CATALOG_READ', 'THROTTLE_ANON', 'THROTTLE_AUTH', 'THROTTLE_OTP'):
                os.environ.pop(key, None)
            defaults = self._load_settings({})
        self.assertEqual(defaults['catalog_read'], '3000/hour')
        self.assertEqual(defaults['anon'], '240/hour')
        self.assertEqual(defaults['auth'], '20/minute')
        self.assertEqual(defaults['otp'], '8/minute')
        custom = self._load_settings({'THROTTLE_CATALOG_READ': '5000/hour', 'THROTTLE_AUTH': '5/minute'})
        self.assertEqual(custom['catalog_read'], '5000/hour')
        self.assertEqual(custom['auth'], '5/minute')

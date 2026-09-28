import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from catalog.models import Product, Review
from seo.utils import build_product_jsonld, build_organization_jsonld, build_website_jsonld


@override_settings(PUBLIC_SITE_URL='https://pinkbakes.com', FRONTEND_URL='https://pinkbakes.com')
class SeoEndpointsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.published = Product.objects.create(
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
        )
        self.draft = Product.objects.create(
            name='Hidden Draft Cake',
            price=Decimal('500.00'),
            status='draft',
            is_active=True,
            available_quantity=10,
        )
        self.inactive = Product.objects.create(
            name='Archived Cake',
            price=Decimal('400.00'),
            status='published',
            is_active=False,
            available_quantity=0,
        )

    def test_robots_txt_allows_public_and_disallows_private(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'].split(';')[0], 'text/plain')
        body = response.content.decode()
        self.assertIn('User-agent: *', body)
        self.assertIn('Allow: /', body)
        self.assertIn('Disallow: /admin', body)
        self.assertIn('Disallow: /api/', body)
        self.assertIn('Disallow: /cart', body)
        self.assertIn('Disallow: /checkout', body)
        self.assertIn('Disallow: /account', body)
        self.assertIn('Sitemap: https://pinkbakes.com/sitemap.xml', body)

    def test_sitemap_includes_home_and_published_products_only(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertIn('xml', response['Content-Type'])
        body = response.content.decode()
        self.assertIn('<urlset', body)
        self.assertIn('https://pinkbakes.com/', body)
        self.assertIn(f'https://pinkbakes.com/products/{self.published.slug}', body)
        self.assertNotIn('Hidden Draft Cake', body)
        self.assertNotIn(self.draft.slug, body)
        self.assertNotIn(self.inactive.slug, body)
        self.assertNotIn('/admin', body)
        self.assertNotIn('/cart', body)
        self.assertNotIn('/api/', body)

    def test_product_schema_json_is_parseable_and_omits_fabricated_reviews(self):
        response = self.client.get(f'/api/seo/product/{self.published.pk}.json')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # Round-trip parseable JSON
        json.loads(json.dumps(payload))
        self.assertEqual(payload['@type'], 'Product')
        self.assertEqual(payload['name'], 'Chocolate Truffle Cake')
        self.assertEqual(payload['offers']['priceCurrency'], 'INR')
        self.assertNotIn('aggregateRating', payload)

        user = User.objects.create_user(username='rev', password='pass12345')
        Review.objects.create(
            product=self.published,
            user=user,
            name='Rev',
            rating=5,
            comment='Great cake',
            status='approved',
        )
        Review.objects.create(
            product=self.published,
            name='Pending',
            rating=1,
            comment='Should not count',
            status='pending',
        )
        response = self.client.get(f'/api/seo/product/{self.published.pk}.json')
        payload = response.json()
        self.assertIn('aggregateRating', payload)
        self.assertEqual(payload['aggregateRating']['reviewCount'], 1)
        self.assertEqual(payload['aggregateRating']['ratingValue'], 5.0)

    def test_product_schema_404_for_draft(self):
        response = self.client.get(f'/api/seo/product/{self.draft.pk}.json')
        self.assertEqual(response.status_code, 404)

    def test_site_schema_has_organization_without_invented_address(self):
        response = self.client.get('/api/seo/site.json')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        org = data['organization']
        self.assertEqual(org['@type'], 'Organization')
        self.assertEqual(org['name'], 'pinkbakes')
        self.assertNotIn('address', org)
        self.assertNotIn('geo', org)
        self.assertNotIn('openingHours', org)
        self.assertNotIn('potentialAction', data['website'])

    def test_build_product_jsonld_escapes_and_skips_empty_rating(self):
        product = self.published
        data = build_product_jsonld(product, average_rating=0, review_count=0)
        self.assertNotIn('aggregateRating', data)
        self.assertEqual(data['offers']['price'], '1080.00')
        org = build_organization_jsonld()
        self.assertEqual(org['url'], 'https://pinkbakes.com')
        site = build_website_jsonld()
        self.assertEqual(site['@type'], 'WebSite')

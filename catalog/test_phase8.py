"""Phase 8: guest delivery quote, product image file upload, site content, delivery slots."""
import io
import shutil
import tempfile
from datetime import time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    Coupon, DeliverySlot, DeliveryZone, GalleryItem, Order, Product, Testimonial,
)
from .site_content import SlotError, resolve_delivery_slot


def _product(**kwargs):
    data = dict(
        name='Phase8 Cake', category='Chocolate Cakes', price=Decimal('500.00'), discount=0,
        short_description='Short', description='Desc', main_image='https://example.com/cake.jpg',
        images=['https://example.com/cake.jpg', 'https://example.com/extra.jpg'],
        availability='in_stock', status='published', is_active=True, available_quantity=50,
        reserved_quantity=0, sold_quantity=0, low_stock_threshold=5,
    )
    data.update(kwargs)
    return Product.objects.create(**data)


def _png_bytes(fmt='PNG'):
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), (200, 100, 150)).save(buf, format=fmt)
    return buf.getvalue()


class GuestDeliveryQuoteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.product = _product()
        self.zone = DeliveryZone.objects.create(
            name='Zone A', postal_codes=['400001'], is_active=True, delivery_charge=Decimal('50.00'),
            minimum_order_amount=Decimal('0.00'), eta_min_minutes=30, eta_max_minutes=60,
        )

    def test_guest_can_quote_by_postal_code(self):
        resp = self.client.post('/api/delivery/quote/', {
            'postal_code': '400001', 'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertTrue(resp.json()['eligible'])
        self.assertEqual(resp.json()['delivery_fee'], 50.0)

    def test_guest_quote_rejects_coupon(self):
        Coupon.objects.create(code='GUEST10', name='Guest', discount_type='percentage', discount_value=Decimal('10'), is_active=True)
        resp = self.client.post('/api/delivery/quote/', {
            'postal_code': '400001', 'items': [{'id': self.product.id, 'quantity': 1}], 'coupon_code': 'GUEST10',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_guest_quote_ignores_address_id(self):
        resp = self.client.post('/api/delivery/quote/', {
            'postal_code': '400001', 'address_id': 999, 'items': [{'id': self.product.id, 'quantity': 1}],
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(resp.json()['postal_code'], '400001')

    def test_guest_quote_uses_delivery_quote_throttle(self):
        from .throttles import DeliveryQuoteAnonThrottle
        from .views import DeliveryQuoteView
        self.assertIn(DeliveryQuoteAnonThrottle, DeliveryQuoteView.throttle_classes)
        self.assertEqual(DeliveryQuoteAnonThrottle.scope, 'delivery_quote')


class ProductImageFileUploadTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.client = APIClient()
        self.admin = User.objects.create_user(username='p8admin', password='x-irrelevant-1', is_staff=True)
        self.product = _product()

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def _url(self):
        return f'/api/catalog/products/{self.product.id}/image/'

    def test_requires_admin(self):
        upload = SimpleUploadedFile('c.png', _png_bytes(), content_type='image/png')
        resp = self.client.post(self._url(), {'image_file': upload}, format='multipart')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_upload_png_sets_main_image(self):
        self.client.force_authenticate(self.admin)
        upload = SimpleUploadedFile('c.png', _png_bytes(), content_type='image/png')
        resp = self.client.post(self._url(), {'image_file': upload}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.product.refresh_from_db()
        self.assertIn('/media/products/', self.product.main_image)
        self.assertTrue(self.product.main_image.endswith('.png'))
        self.assertTrue(self.product.main_image.startswith('http'))
        self.assertEqual(self.product.images[0], self.product.main_image)
        self.assertNotIn('https://example.com/cake.jpg', self.product.images)
        self.assertIn('https://example.com/extra.jpg', self.product.images)

    def test_rejects_non_image(self):
        self.client.force_authenticate(self.admin)
        upload = SimpleUploadedFile('c.png', b'not an image at all', content_type='image/png')
        resp = self.client.post(self._url(), {'image_file': upload}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_unsupported_format(self):
        self.client.force_authenticate(self.admin)
        upload = SimpleUploadedFile('c.gif', _png_bytes('GIF'), content_type='image/gif')
        resp = self.client.post(self._url(), {'image_file': upload}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(PRODUCT_IMAGE_MAX_BYTES=10)
    def test_rejects_too_large(self):
        self.client.force_authenticate(self.admin)
        upload = SimpleUploadedFile('c.png', _png_bytes(), content_type='image/png')
        resp = self.client.post(self._url(), {'image_file': upload}, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_url_option_still_works(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(self._url(), {'image': 'https://example.com/new.jpg'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.product.refresh_from_db()
        self.assertEqual(self.product.main_image, 'https://example.com/new.jpg')


class SiteContentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(username='p8staff', password='x-irrelevant-2', is_staff=True)
        self.user = User.objects.create_user(username='p8user', password='x-irrelevant-3')

    def test_public_lists_only_active_in_order(self):
        GalleryItem.objects.create(image_url='https://example.com/b.jpg', alt_text='B', sort_order=2)
        GalleryItem.objects.create(image_url='https://example.com/a.jpg', alt_text='A', sort_order=1)
        GalleryItem.objects.create(image_url='https://example.com/x.jpg', alt_text='X', is_active=False)
        Testimonial.objects.create(name='Asha', text='Lovely', tag='Birthday')
        Testimonial.objects.create(name='Hidden', text='No', is_active=False)
        gallery = self.client.get('/api/catalog/site/gallery/')
        self.assertEqual(gallery.status_code, 200)
        self.assertEqual([g['alt_text'] for g in gallery.json()], ['A', 'B'])
        testimonials = self.client.get('/api/catalog/site/testimonials/')
        self.assertEqual([t['name'] for t in testimonials.json()], ['Asha'])

    def test_admin_crud_requires_staff(self):
        payload = {'image_url': 'https://example.com/c.jpg', 'alt_text': 'Cake', 'caption': 'Birthday'}
        self.assertIn(self.client.post('/api/catalog/admin/gallery/', payload, format='json').status_code, (401, 403))
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post('/api/catalog/admin/gallery/', payload, format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        created = self.client.post('/api/catalog/admin/gallery/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        gid = created.json()['id']
        patched = self.client.patch(f'/api/catalog/admin/gallery/{gid}/', {'is_active': False}, format='json')
        self.assertFalse(patched.json()['is_active'])
        self.assertEqual(self.client.get('/api/catalog/admin/gallery/').json()['count'], 1)
        self.assertEqual(self.client.delete(f'/api/catalog/admin/gallery/{gid}/').status_code, 204)
        self.assertFalse(GalleryItem.objects.exists())

    def test_validation(self):
        self.client.force_authenticate(self.admin)
        bad_url = self.client.post('/api/catalog/admin/gallery/', {'image_url': 'http://example.com/c.jpg', 'alt_text': 'x'}, format='json')
        self.assertEqual(bad_url.status_code, 400)
        bad_rating = self.client.post('/api/catalog/admin/testimonials/', {'name': 'A', 'text': 'B', 'rating': 9}, format='json')
        self.assertEqual(bad_rating.status_code, 400)
        bad_slot = self.client.post('/api/catalog/admin/delivery-slots/', {'label': 'X', 'start_time': '12:00', 'end_time': '10:00'}, format='json')
        self.assertEqual(bad_slot.status_code, 400)


class DeliverySlotTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='slotuser', email='slot@example.com', password='x-irrelevant-4')
        self.product = _product(name='Slot Cake')
        self.slot = DeliverySlot.objects.create(label='Evening 6-9 PM', start_time=time(18, 0), end_time=time(21, 0))
        self.inactive = DeliverySlot.objects.create(label='Night', start_time=time(21, 0), end_time=time(23, 0), is_active=False)

    def _tomorrow(self):
        return (timezone.localtime(timezone=ZoneInfo('Asia/Kolkata')).date() + timedelta(days=1)).isoformat()

    def _checkout(self, **extra):
        payload = {
            'items': [{'id': self.product.id, 'quantity': 1}],
            'customer_name': 'Slot User', 'customer_email': 'slot@example.com', 'customer_mobile': '9876543210',
            'shipping_address': '12 Market Road', 'city': 'Mumbai', 'state': 'Maharashtra',
            'postal_code': '400001', 'country': 'India',
        }
        payload.update(extra)
        self.client.force_authenticate(self.user)
        return self.client.post('/api/orders/checkout/', payload, format='json')

    def test_public_slots_list_active_only(self):
        resp = self.client.get('/api/catalog/delivery/slots/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([s['label'] for s in resp.json()], ['Evening 6-9 PM'])

    def test_checkout_without_slot_still_works(self):
        resp = self._checkout()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        order = Order.objects.latest('id')
        self.assertIsNone(order.delivery_slot_id)
        self.assertEqual(order.delivery_slot_label, '')

    def test_checkout_with_slot_snapshots_label(self):
        resp = self._checkout(delivery_slot_id=self.slot.id, delivery_date=self._tomorrow())
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        order = Order.objects.latest('id')
        self.assertEqual(order.delivery_slot_id, self.slot.id)
        self.assertEqual(order.delivery_slot_label, 'Evening 6-9 PM')
        self.assertEqual(order.delivery_date.isoformat(), self._tomorrow())

    def test_checkout_rejects_bad_slot_inputs(self):
        self.assertEqual(self._checkout(delivery_slot_id=self.inactive.id, delivery_date=self._tomorrow()).status_code, 400)
        self.assertEqual(self._checkout(delivery_slot_id=self.slot.id).status_code, 400)
        self.assertEqual(self._checkout(delivery_date=self._tomorrow()).status_code, 400)
        self.assertEqual(self._checkout(delivery_slot_id=self.slot.id, delivery_date='2000-01-01').status_code, 400)
        self.assertEqual(self._checkout(delivery_slot_id=self.slot.id, delivery_date='not-a-date').status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_resolve_rejects_far_future(self):
        far = (timezone.localtime(timezone=ZoneInfo('Asia/Kolkata')).date() + timedelta(days=60)).isoformat()
        with self.assertRaises(SlotError):
            resolve_delivery_slot({'delivery_slot_id': self.slot.id, 'delivery_date': far})
        self.assertEqual(resolve_delivery_slot({}), {})


class AdminOrderListQueryTests(TestCase):
    def test_order_list_query_count_does_not_grow_per_order(self):
        client = APIClient()
        admin = User.objects.create_user(username='qadmin', password='x-irrelevant-5', is_staff=True)
        customer = User.objects.create_user(username='qcust', email='q@example.com', password='x-irrelevant-6')
        client.force_authenticate(admin)

        def make_order(n):
            Order.objects.create(
                user=customer, order_number=f'PB-Q-{n}', customer_name='Q', customer_email='q@example.com',
                customer_mobile='9876543210', shipping_address='Road', city='Mumbai', state='MH',
                postal_code='400001', country='India', total_amount=Decimal('100'),
            )

        make_order(1)
        first = self._count(client)
        for n in range(2, 6):
            make_order(n)
        later = self._count(client)
        self.assertEqual(first, later)

    def _count(self, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get('/api/catalog/admin/orders/')
        self.assertEqual(resp.status_code, 200, resp.content)
        return len(ctx.captured_queries)


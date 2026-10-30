"""Phase 14: SMS / WhatsApp / staff-alert notification tests. All providers are mocked; nothing is sent."""

import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from notifications import events as E
from notifications.channels.sms import SmsChannelAdapter
from notifications.channels.whatsapp import WhatsAppChannelAdapter
from notifications.defaults import seed_channel_configs, seed_email_templates
from notifications.models import NotificationLog, NotificationPreference
from notifications.phone import normalize_phone
from notifications.service import notify, notify_low_stock, notify_order_placed, notify_staff_new_order

TWILIO = dict(
    SMS_PROVIDER='twilio',
    TWILIO_ACCOUNT_SID='AC00000000000000000000000000000000',
    TWILIO_AUTH_TOKEN='test-token',
    TWILIO_FROM_NUMBER='+15551234567',
)
META = dict(
    WHATSAPP_PROVIDER='meta',
    WHATSAPP_TOKEN='test-wa-token',
    WHATSAPP_PHONE_NUMBER_ID='1234567890',
    WHATSAPP_API_URL='',
    WHATSAPP_TEMPLATE_NAME='order_update',
    WHATSAPP_TEMPLATE_LANGUAGE='en',
)
NO_PROVIDERS = dict(
    SMS_PROVIDER='', TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='', TWILIO_FROM_NUMBER='',
    WHATSAPP_PROVIDER='', WHATSAPP_TOKEN='', WHATSAPP_PHONE_NUMBER_ID='', TWILIO_WHATSAPP_FROM='',
)
LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def _meta_ok(*args, **kwargs):
    return 200, {'messages': [{'id': 'wamid.TEST'}]}


class PhoneNormalizationTests(TestCase):
    @override_settings(NOTIFICATION_DEFAULT_COUNTRY_CODE='91')
    def test_indian_formats(self):
        self.assertEqual(normalize_phone('9876543210'), '+919876543210')
        self.assertEqual(normalize_phone('09876543210'), '+919876543210')
        self.assertEqual(normalize_phone('919876543210'), '+919876543210')
        self.assertEqual(normalize_phone('+91 98765-43210'), '+919876543210')
        self.assertEqual(normalize_phone('whatsapp:+15557654321'), '+15557654321')
        self.assertEqual(normalize_phone('12345'), '')
        self.assertEqual(normalize_phone(''), '')


class Phase14Base(TestCase):
    def setUp(self):
        seed_channel_configs()
        seed_email_templates()
        from accounts.models import UserProfile
        from catalog.models import Order
        self.user = User.objects.create_user(username='p14cust', email='p14@example.com', password='Pass12345!')
        UserProfile.objects.get_or_create(user=self.user, defaults={'mobile_number': '9876543210'})
        self.staff = User.objects.create_user(username='p14staff', email='staff@example.com', password='Pass12345!', is_staff=True)
        UserProfile.objects.get_or_create(user=self.staff, defaults={'mobile_number': '9123456780'})
        self.order = Order.objects.create(
            user=self.user, order_number='PB-P14-001', customer_name='Asha',
            customer_email='p14@example.com', customer_mobile='9876543210',
            shipping_address='1 Cake Lane', city='Kolkata', state='WB', postal_code='700001', country='India',
            subtotal_amount=Decimal('450.00'), total_amount=Decimal('450.00'),
            status='PENDING', payment_status='pending',
        )


@override_settings(EMAIL_BACKEND=LOCMEM, **NO_PROVIDERS)
class NoCredentialsFallbackTests(Phase14Base):
    def test_order_placed_without_providers_emails_and_skips_sms_whatsapp(self):
        mail.outbox.clear()
        results = notify_order_placed(self.order)
        by_channel = {r['channel']: r['status'] for r in results}
        self.assertEqual(by_channel['email'], E.STATUS_SENT)
        self.assertEqual(by_channel['sms'], E.STATUS_SKIPPED)
        self.assertEqual(by_channel['whatsapp'], E.STATUS_SKIPPED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('PB-P14-001', mail.outbox[0].subject)
        self.assertEqual(
            NotificationLog.objects.get(event=E.ORDER_CREATED, channel='whatsapp').last_error,
            'whatsapp_unconfigured',
        )


@override_settings(EMAIL_BACKEND=LOCMEM, **TWILIO, **META)
class ProviderSendTests(Phase14Base):
    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_order_placed_sends_sms_and_whatsapp_to_normalized_number(self, client_class, http_post):
        client_class.return_value.messages.create.return_value.sid = 'SM1'
        results = notify_order_placed(self.order)
        self.assertTrue(all(r['status'] == E.STATUS_SENT for r in results if r['channel'] in ('sms', 'whatsapp', 'email')))
        kwargs = client_class.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs['to'], '+919876543210')
        self.assertIn('PB-P14-001', kwargs['body'])
        url, payload, headers = http_post.call_args.args
        self.assertEqual(url, 'https://graph.facebook.com/v21.0/1234567890/messages')
        self.assertEqual(payload['to'], '919876543210')
        self.assertEqual(payload['template']['name'], 'order_update')
        self.assertIn('PB-P14-001', payload['template']['components'][0]['parameters'][0]['text'])
        self.assertEqual(headers['Authorization'], 'Bearer test-wa-token')
        # Logs never hold the full number.
        log = NotificationLog.objects.get(event=E.ORDER_CREATED, channel='sms')
        self.assertNotIn('9876543210', log.recipient)

    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_idempotent_no_duplicate_sends(self, client_class, http_post):
        notify_order_placed(self.order)
        notify_order_placed(self.order)
        self.assertEqual(client_class.return_value.messages.create.call_count, 1)
        self.assertEqual(http_post.call_count, 1)

    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_preference_opt_out_blocks_sms_and_whatsapp(self, client_class, http_post):
        prefs, _ = NotificationPreference.objects.get_or_create(user=self.user)
        prefs.sms_order_updates = False
        prefs.save()
        results = notify_order_placed(self.order)
        by_channel = {r['channel']: r for r in results}
        self.assertEqual(by_channel['sms']['reason'], 'preference')
        self.assertEqual(by_channel['whatsapp']['reason'], 'preference')
        client_class.return_value.messages.create.assert_not_called()
        http_post.assert_not_called()

    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=RuntimeError('network down'))
    @mock.patch('notifications.channels.sms.Client')
    def test_provider_failure_is_logged_not_raised(self, client_class, http_post):
        client_class.return_value.messages.create.side_effect = RuntimeError('twilio down')
        results = notify_order_placed(self.order)
        by_channel = {r['channel']: r['status'] for r in results}
        self.assertEqual(by_channel['sms'], E.STATUS_FAILED)
        self.assertEqual(by_channel['whatsapp'], E.STATUS_FAILED)
        self.assertEqual(http_post.call_count, 2)  # one retry
        self.assertEqual(NotificationLog.objects.get(event=E.ORDER_CREATED, channel='sms').retry_count, 1)

    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_otp_never_sent_over_whatsapp(self, client_class, http_post):
        result = WhatsAppChannelAdapter().send(event=E.LOGIN_OTP_REQUESTED, phone='9876543210', context={'otp': '111222'})
        self.assertTrue(result['skipped'])
        http_post.assert_not_called()

    @override_settings(NOTIFICATION_STAFF_PHONES='9000000001, +91 91234 56780')
    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_staff_new_order_alert_reaches_staff_phones_deduped(self, client_class, http_post):
        mail.outbox.clear()
        notify_staff_new_order(self.order)
        sms_to = sorted(c.kwargs['to'] for c in client_class.return_value.messages.create.call_args_list)
        self.assertEqual(sms_to, ['+919000000001', '+919123456780'])
        wa_to = sorted(c.args[1]['to'] for c in http_post.call_args_list)
        self.assertEqual(wa_to, ['919000000001', '919123456780'])
        self.assertEqual([m.to for m in mail.outbox], [['staff@example.com']])
        # Second call is idempotent for every target.
        notify_staff_new_order(self.order)
        self.assertEqual(client_class.return_value.messages.create.call_count, 2)

    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_low_stock_whatsapp_to_staff(self, client_class, http_post):
        from catalog.models import Product
        product = Product.objects.create(name='Red Velvet', price=Decimal('300.00'), category='Cakes',
                                         available_quantity=2, low_stock_threshold=5)
        notify_low_stock(product)
        self.assertEqual(http_post.call_count, 1)
        self.assertIn('Red Velvet', http_post.call_args.args[1]['template']['components'][0]['parameters'][0]['text'])


@override_settings(EMAIL_BACKEND=LOCMEM, WHATSAPP_PROVIDER='twilio', TWILIO_WHATSAPP_FROM='+14155238886',
                   **{k: v for k, v in TWILIO.items()})
class TwilioWhatsAppTests(Phase14Base):
    @mock.patch('notifications.channels.sms.Client')
    def test_twilio_whatsapp_prefixes(self, client_class):
        client_class.return_value.messages.create.return_value.sid = 'SMWA'
        result = WhatsAppChannelAdapter().send(event=E.ORDER_CONFIRMED, phone='9876543210', context={'message': 'Confirmed'})
        self.assertTrue(result['ok'])
        kwargs = client_class.return_value.messages.create.call_args.kwargs
        self.assertEqual(kwargs['from_'], 'whatsapp:+14155238886')
        self.assertEqual(kwargs['to'], 'whatsapp:+919876543210')


@override_settings(EMAIL_BACKEND=LOCMEM, SMS_PROVIDER='console', WHATSAPP_PROVIDER='console')
class ConsoleProviderTests(TestCase):
    def test_console_logs_without_sending(self):
        r1 = SmsChannelAdapter().send(event=E.ORDER_CONFIRMED, phone='9876543210', context={'message': 'hi'})
        r2 = WhatsAppChannelAdapter().send(event=E.ORDER_CONFIRMED, phone='9876543210', context={'message': 'hi'})
        self.assertEqual(r1['error'], 'console_provider')
        self.assertEqual(r2['error'], 'console_provider')


@override_settings(EMAIL_BACKEND=LOCMEM, **TWILIO, **META)
class OrderFlowTriggerTests(Phase14Base):
    def _checkout(self):
        from catalog.models import Product
        product = Product.objects.create(
            name='Truffle', category='Cakes', price=Decimal('500.00'), main_image='https://example.com/c.jpg',
            availability='in_stock', status='published', is_active=True, available_quantity=20, low_stock_threshold=2,
        )
        client = APIClient()
        client.force_authenticate(user=self.user)
        return client.post('/api/orders/checkout/', {
            'items': [{'id': product.id, 'quantity': 1}],
            'customer_name': 'Asha', 'customer_email': 'p14@example.com', 'customer_mobile': '9876543210',
            'shipping_address': '1 Cake Lane', 'city': 'Mumbai', 'state': 'Maharashtra',
            'postal_code': '400001', 'country': 'India',
        }, format='json')

    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_checkout_without_payment_notifies_customer_and_staff(self, client_class, http_post):
        response = self._checkout()
        self.assertEqual(response.status_code, 201, response.content)
        order_id = str(response.json()['id'])
        self.assertTrue(NotificationLog.objects.filter(event=E.ORDER_CREATED, channel='sms', status='sent', reference_id=order_id).exists())
        self.assertTrue(NotificationLog.objects.filter(event=E.ORDER_CREATED, channel='whatsapp', status='sent', reference_id=order_id).exists())
        self.assertTrue(NotificationLog.objects.filter(event=E.NEW_ORDER, channel='email', status='sent').exists())
        self.assertTrue(NotificationLog.objects.filter(event=E.NEW_ORDER, channel='sms', status='sent').exists())

    @mock.patch('notifications.service.notify', side_effect=RuntimeError('boom'))
    def test_checkout_succeeds_even_if_notifications_crash(self, _notify):
        response = self._checkout()
        self.assertEqual(response.status_code, 201, response.content)

    @mock.patch('notifications.channels.whatsapp._http_post_json', side_effect=_meta_ok)
    @mock.patch('notifications.channels.sms.Client')
    def test_admin_status_changes_trigger_customer_sms_whatsapp(self, client_class, http_post):
        admin = User.objects.create_user(username='p14admin', password='Pass12345!', is_staff=True, is_superuser=True)
        client = APIClient()
        client.force_authenticate(user=admin)
        for new_status, event in (('ORDER_CONFIRMED', E.ORDER_CONFIRMED),):
            resp = client.post(f'/api/admin/orders/{self.order.pk}/status/', {'status': new_status}, format='json')
            if resp.status_code == 405:
                resp = client.patch(f'/api/admin/orders/{self.order.pk}/status/', {'status': new_status}, format='json')
            self.assertEqual(resp.status_code, 200, resp.content)
            for ch in ('sms', 'whatsapp', 'email'):
                self.assertTrue(NotificationLog.objects.filter(event=event, channel=ch, status='sent', reference_id=str(self.order.pk)).exists(), ch)

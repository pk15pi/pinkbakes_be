"""WhatsApp channel.

Providers (settings.WHATSAPP_PROVIDER):
  meta    - WhatsApp Business Cloud API (graph.facebook.com). Business-initiated messages need an
            approved template; set WHATSAPP_TEMPLATE_NAME to a UTILITY template with one body
            variable {{1}} which receives the notification text. Without a template name a plain
            text message is sent (only delivered inside the 24h customer-service window).
  twilio  - Twilio WhatsApp sender (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN + TWILIO_WHATSAPP_FROM).
  console - log metadata only (dev).
  ''      - auto: meta when token + phone number id are set, else twilio when its WhatsApp sender is set.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

from django.conf import settings

from notifications import events as E
from notifications.phone import mask_phone, resolve_phone

logger = logging.getLogger(__name__)

DEFAULT_GRAPH_URL = 'https://graph.facebook.com/v21.0'
SUPPORTED_PROVIDERS = ('meta', 'twilio', 'console')

# Never send OTPs / reset links over WhatsApp from this generic adapter.
_NO_WHATSAPP_EVENTS = frozenset({
    E.LOGIN_OTP_REQUESTED, E.PASSWORD_RESET_REQUESTED,
    E.EMAIL_VERIFICATION_REQUIRED, E.MOBILE_VERIFICATION_REQUIRED,
})


def _s(name):
    return (getattr(settings, name, '') or '').strip()


def _template_param(text: str) -> str:
    # Meta rejects template params containing newlines/tabs or >4 consecutive spaces.
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r' {4,}', '   ', text)
    return text[:1000]


def _http_post_json(url, payload, headers, timeout=10):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST', headers={'Content-Type': 'application/json', **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoint from settings
        body = resp.read().decode('utf-8') or '{}'
        return resp.status, json.loads(body)


class WhatsAppChannelAdapter:
    channel = E.CHANNEL_WHATSAPP

    def provider(self) -> str:
        provider = _s('WHATSAPP_PROVIDER').lower()
        if provider:
            return provider
        if _s('WHATSAPP_TOKEN') and _s('WHATSAPP_PHONE_NUMBER_ID'):
            return 'meta'
        if _s('TWILIO_ACCOUNT_SID') and _s('TWILIO_AUTH_TOKEN') and _s('TWILIO_WHATSAPP_FROM'):
            return 'twilio'
        return ''

    def is_configured(self) -> bool:
        provider = self.provider()
        if provider == 'meta':
            return bool(_s('WHATSAPP_TOKEN') and _s('WHATSAPP_PHONE_NUMBER_ID'))
        if provider == 'twilio':
            return bool(_s('TWILIO_ACCOUNT_SID') and _s('TWILIO_AUTH_TOKEN') and _s('TWILIO_WHATSAPP_FROM'))
        return provider == 'console'

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        if not self.is_configured():
            logger.info('WhatsApp skipped (unconfigured) event=%s', event)
            return {'ok': False, 'skipped': True, 'error': 'whatsapp_unconfigured'}
        if event in _NO_WHATSAPP_EVENTS:
            return {'ok': False, 'skipped': True, 'error': 'event_not_supported_on_whatsapp'}

        context = context or {}
        recipient = resolve_phone(phone, user, context)
        if not recipient:
            return {'ok': False, 'skipped': True, 'error': 'no_recipient'}

        from notifications.channels.sms import build_message
        message = build_message(event, context, subject, body_text)
        if not message:
            return {'ok': False, 'skipped': True, 'error': 'no_message'}

        provider = self.provider()
        try:
            if provider == 'console':
                logger.info('WhatsApp console provider event=%s to=%s chars=%s', event, mask_phone(recipient), len(message))
                return {'ok': False, 'skipped': True, 'error': 'console_provider', 'recipient': recipient}
            if provider == 'twilio':
                return self._send_twilio(recipient, message)
            return self._send_meta(recipient, message)
        except Exception as exc:
            logger.warning('WhatsApp send failed provider=%s event=%s to=%s err=%s', provider, event, mask_phone(recipient), exc.__class__.__name__)
            return {'ok': False, 'skipped': False, 'error': str(exc)[:500], 'recipient': recipient}

    def _send_meta(self, recipient, message):
        base = (_s('WHATSAPP_API_URL') or DEFAULT_GRAPH_URL).rstrip('/')
        url = f"{base}/{_s('WHATSAPP_PHONE_NUMBER_ID')}/messages"
        to = recipient.lstrip('+')
        template = _s('WHATSAPP_TEMPLATE_NAME')
        if template:
            payload = {
                'messaging_product': 'whatsapp',
                'to': to,
                'type': 'template',
                'template': {
                    'name': template,
                    'language': {'code': _s('WHATSAPP_TEMPLATE_LANGUAGE') or 'en'},
                    'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': _template_param(message)}]}],
                },
            }
        else:
            payload = {'messaging_product': 'whatsapp', 'to': to, 'type': 'text', 'text': {'body': message[:4096]}}
        try:
            status, data = _http_post_json(url, payload, {'Authorization': f"Bearer {_s('WHATSAPP_TOKEN')}"})
        except urllib.error.HTTPError as exc:
            detail = ''
            try:
                detail = json.loads(exc.read().decode('utf-8')).get('error', {}).get('message', '')
            except Exception:
                pass
            return {'ok': False, 'skipped': False, 'error': f'meta_http_{exc.code}: {detail}'[:500], 'recipient': recipient}
        message_id = ''
        if isinstance(data, dict) and data.get('messages'):
            message_id = data['messages'][0].get('id', '')
        if 200 <= status < 300 and message_id:
            return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient, 'message_id': message_id}
        return {'ok': False, 'skipped': False, 'error': f'meta_unexpected_response_{status}', 'recipient': recipient}

    def _send_twilio(self, recipient, message):
        from notifications.channels import sms as sms_module
        if sms_module.Client is None:
            return {'ok': False, 'skipped': True, 'error': 'twilio_not_installed'}
        sender = _s('TWILIO_WHATSAPP_FROM')
        if not sender.lower().startswith('whatsapp:'):
            sender = f'whatsapp:{sender}'
        client = sms_module.Client(_s('TWILIO_ACCOUNT_SID'), _s('TWILIO_AUTH_TOKEN'))
        msg = client.messages.create(body=message[:1600], from_=sender, to=f'whatsapp:{recipient}')
        return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient, 'message_sid': getattr(msg, 'sid', '')}

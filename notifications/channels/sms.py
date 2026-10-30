"""SMS channel. Provider chosen by settings.SMS_PROVIDER (twilio | console). Credentials come from env only."""

from __future__ import annotations

import logging

from django.conf import settings

try:  # twilio is in requirements.txt; keep the module importable if it is missing locally.
    from twilio.rest import Client
except Exception:  # pragma: no cover - only hit when the package is absent
    Client = None

from notifications import events as E
from notifications.phone import mask_phone, resolve_phone
from notifications.rendering import flatten_context, render_template

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ('twilio', 'console')

# Never push secrets through a third-party message body except the login OTP SMS, which is its purpose.
_NO_SMS_EVENTS = frozenset({E.PASSWORD_RESET_REQUESTED, E.EMAIL_VERIFICATION_REQUIRED})


def _s(name):
    return (getattr(settings, name, '') or '').strip()


def build_message(event, context, subject, body_text):
    context = context or {}
    if body_text:
        rendered = render_template(body_text, flatten_context(context)).strip()
        if rendered:
            return rendered
    return str(context.get('message') or subject or '').strip()


class SmsChannelAdapter:
    channel = E.CHANNEL_SMS

    def provider(self) -> str:
        provider = _s('SMS_PROVIDER').lower()
        if provider:
            return provider
        if _s('TWILIO_ACCOUNT_SID') and _s('TWILIO_AUTH_TOKEN') and _s('TWILIO_FROM_NUMBER'):
            return 'twilio'
        return ''

    def is_configured(self) -> bool:
        provider = self.provider()
        if provider == 'twilio':
            return bool(Client and _s('TWILIO_ACCOUNT_SID') and _s('TWILIO_AUTH_TOKEN') and _s('TWILIO_FROM_NUMBER'))
        return provider == 'console'

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        if not self.is_configured():
            logger.info('SMS skipped (unconfigured) event=%s', event)
            return {'ok': False, 'skipped': True, 'error': 'sms_unconfigured'}
        if event in _NO_SMS_EVENTS:
            return {'ok': False, 'skipped': True, 'error': 'event_not_supported_on_sms'}

        context = context or {}
        recipient = resolve_phone(phone, user, context)
        if not recipient:
            return {'ok': False, 'skipped': True, 'error': 'no_recipient'}

        if event == E.LOGIN_OTP_REQUESTED:
            otp = str(context.get('otp') or '').strip()
            if not otp:
                return {'ok': False, 'skipped': True, 'error': 'missing_otp'}
            message_body = f'Your PinkBakes login OTP is {otp}. It expires in 10 minutes.'
        else:
            message_body = build_message(event, context, subject, body_text)
        if not message_body:
            return {'ok': False, 'skipped': True, 'error': 'no_message'}

        if self.provider() == 'console':
            # Dev/staging: log metadata only (never the body, which may hold an OTP).
            logger.info('SMS console provider event=%s to=%s chars=%s', event, mask_phone(recipient), len(message_body))
            return {'ok': False, 'skipped': True, 'error': 'console_provider', 'recipient': recipient}

        try:
            client = Client(_s('TWILIO_ACCOUNT_SID'), _s('TWILIO_AUTH_TOKEN'))
            message = client.messages.create(body=message_body, from_=_s('TWILIO_FROM_NUMBER'), to=recipient)
            return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient, 'message_sid': getattr(message, 'sid', '')}
        except Exception as exc:
            logger.warning('Twilio SMS send failed event=%s to=%s err=%s', event, mask_phone(recipient), exc.__class__.__name__)
            return {'ok': False, 'skipped': False, 'error': str(exc)[:500], 'recipient': recipient}

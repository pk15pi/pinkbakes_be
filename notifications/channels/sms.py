from __future__ import annotations

import logging

from django.conf import settings
from twilio.rest import Client

from notifications import events as E
from notifications.rendering import flatten_context, render_template

logger = logging.getLogger(__name__)


class SmsChannelAdapter:
    channel = E.CHANNEL_SMS

    def is_configured(self) -> bool:
        provider = (getattr(settings, 'SMS_PROVIDER', '') or '').strip().lower()
        if provider and provider != 'twilio':
            return False
        return all((
            (getattr(settings, 'TWILIO_ACCOUNT_SID', '') or '').strip(),
            (getattr(settings, 'TWILIO_AUTH_TOKEN', '') or '').strip(),
            (getattr(settings, 'TWILIO_FROM_NUMBER', '') or '').strip(),
        ))

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        if not self.is_configured():
            logger.info('SMS skipped (unconfigured) event=%s', event)
            return {'ok': False, 'skipped': True, 'error': 'sms_unconfigured'}

        context = context or {}
        recipient = (phone or '').strip()
        if not recipient and user is not None:
            profile = getattr(user, 'profile', None)
            recipient = (getattr(profile, 'mobile_number', '') or '').strip()
        if not recipient:
            return {'ok': False, 'skipped': True, 'error': 'no_recipient'}

        if event == E.LOGIN_OTP_REQUESTED:
            otp = str(context.get('otp') or '').strip()
            if not otp:
                return {'ok': False, 'skipped': True, 'error': 'missing_otp'}
            message_body = f'Your PinkBakes login OTP is {otp}. It expires in 10 minutes.'
        else:
            template_context = flatten_context(context)
            message_body = (
                render_template(body_text, template_context) if body_text else ''
            ) or str(context.get('message') or subject or '').strip()
        if not message_body:
            return {'ok': False, 'skipped': True, 'error': 'no_message'}

        try:
            client = Client(
                (settings.TWILIO_ACCOUNT_SID or '').strip(),
                (settings.TWILIO_AUTH_TOKEN or '').strip(),
            )
            message = client.messages.create(
                body=message_body,
                from_=(settings.TWILIO_FROM_NUMBER or '').strip(),
                to=recipient,
            )
            return {
                'ok': True,
                'skipped': False,
                'error': '',
                'recipient': recipient,
                'message_sid': message.sid,
            }
        except Exception as exc:
            logger.exception('Twilio SMS send failed for %s', event)
            return {
                'ok': False,
                'skipped': False,
                'error': str(exc)[:500],
                'recipient': recipient,
            }

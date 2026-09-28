from __future__ import annotations

import logging
import os

from notifications import events as E

logger = logging.getLogger(__name__)


class SmsChannelAdapter:
    channel = E.CHANNEL_SMS

    def is_configured(self) -> bool:
        provider = (os.environ.get('SMS_PROVIDER') or '').strip()
        twilio_sid = (os.environ.get('TWILIO_ACCOUNT_SID') or '').strip()
        twilio_token = (os.environ.get('TWILIO_AUTH_TOKEN') or '').strip()
        return bool(provider or (twilio_sid and twilio_token))

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        if not self.is_configured():
            logger.info('SMS skipped (unconfigured) event=%s', event)
            return {'ok': False, 'skipped': True, 'error': 'sms_unconfigured'}
        # Provider-agnostic stub: configured envs present but no live send implemented yet.
        logger.info('SMS stub send event=%s phone=%s', event, bool(phone))
        return {'ok': False, 'skipped': True, 'error': 'sms_stub_no_provider_impl'}

from __future__ import annotations

import logging
import os

from notifications import events as E

logger = logging.getLogger(__name__)


class WhatsAppChannelAdapter:
    channel = E.CHANNEL_WHATSAPP

    def is_configured(self) -> bool:
        return bool(
            (os.environ.get('WHATSAPP_API_URL') or '').strip()
            or (os.environ.get('WHATSAPP_TOKEN') or '').strip()
            or (os.environ.get('WHATSAPP_PHONE_NUMBER_ID') or '').strip()
        )

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        if not self.is_configured():
            logger.info('WhatsApp skipped (unconfigured) event=%s', event)
            return {'ok': False, 'skipped': True, 'error': 'whatsapp_unconfigured'}
        logger.info('WhatsApp stub send event=%s', event)
        return {'ok': False, 'skipped': True, 'error': 'whatsapp_stub_no_provider_impl'}

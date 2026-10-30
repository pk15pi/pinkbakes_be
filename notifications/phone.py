"""Phone-number helpers for SMS / WhatsApp channels (E.164 normalisation, masking)."""

from __future__ import annotations

import re

from django.conf import settings

_DIGITS = re.compile(r'\D+')


def default_country_code() -> str:
    raw = str(getattr(settings, 'NOTIFICATION_DEFAULT_COUNTRY_CODE', '91') or '91')
    return _DIGITS.sub('', raw) or '91'


def normalize_phone(raw) -> str:
    """Return an E.164 number ("+919876543210") or '' when the input is unusable.

    Local numbers (10 digits, or 0-prefixed trunk numbers) get the default country code
    from settings.NOTIFICATION_DEFAULT_COUNTRY_CODE (India, 91, unless configured).
    """
    value = str(raw or '').strip()
    if not value:
        return ''
    if value.lower().startswith('whatsapp:'):
        value = value.split(':', 1)[1]
    has_plus = value.startswith('+')
    digits = _DIGITS.sub('', value)
    if not digits:
        return ''
    cc = default_country_code()
    if has_plus:
        pass
    elif digits.startswith('00'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = cc + digits[1:]
    elif len(digits) == 10:
        digits = cc + digits
    elif digits.startswith(cc) and len(digits) == len(cc) + 10:
        pass
    else:
        return ''
    if not 8 <= len(digits) <= 15:
        return ''
    return f'+{digits}'


def resolve_phone(phone=None, user=None, context=None) -> str:
    """Pick the first usable number: explicit phone, user profile mobile, order customer mobile."""
    candidates = [phone]
    if user is not None:
        profile = getattr(user, 'profile', None)
        candidates.append(getattr(profile, 'mobile_number', '') if profile is not None else '')
    order = (context or {}).get('order')
    if order is not None:
        candidates.append(getattr(order, 'customer_mobile', ''))
    for candidate in candidates:
        normalized = normalize_phone(candidate)
        if normalized:
            return normalized
    return ''


def staff_alert_phones() -> list:
    raw = getattr(settings, 'NOTIFICATION_STAFF_PHONES', '') or ''
    out = []
    for part in str(raw).split(','):
        normalized = normalize_phone(part)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def mask_phone(value: str) -> str:
    value = str(value or '')
    if len(value) <= 4:
        return '***'
    return f'{value[:3]}***{value[-2:]}'

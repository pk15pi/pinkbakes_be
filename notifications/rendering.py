"""Simple {{var}} substitution — no Jinja dependency."""

from __future__ import annotations

import re

_PATTERN = re.compile(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}')

# Keys that must never appear in logs or rendered debug dumps.
SENSITIVE_KEYS = frozenset({
    'otp', 'otp_code', 'password', 'token', 'verification_token',
    'reset_token', 'smtp_password', 'email_host_password', 'secret',
    'webhook_secret', 'api_key', 'authorization',
})


def render_template(template: str, context: dict | None = None) -> str:
    if not template:
        return ''
    ctx = context or {}

    def repl(match):
        key = match.group(1)
        value = ctx.get(key, '')
        if value is None:
            return ''
        return str(value)

    return _PATTERN.sub(repl, template)


def safe_context_snapshot(context: dict | None) -> dict:
    """Mask secrets for any debug use — never store OTP/passwords."""
    out = {}
    for key, value in (context or {}).items():
        if key.lower() in SENSITIVE_KEYS or any(s in key.lower() for s in ('password', 'secret', 'token', 'otp')):
            out[key] = '***'
        elif hasattr(value, 'pk'):
            out[key] = f'<{value.__class__.__name__}:{value.pk}>'
        else:
            try:
                out[key] = str(value)[:200]
            except Exception:
                out[key] = '<unrepr>'
    return out


def flatten_context(context: dict | None) -> dict:
    """Build a flat string-friendly dict for {{var}} templates from nested objects."""
    ctx = dict(context or {})
    flat = {}
    for key, value in ctx.items():
        if key.lower() in SENSITIVE_KEYS:
            continue
        if hasattr(value, 'pk') and key == 'order':
            order = value
            flat.setdefault('order_number', getattr(order, 'order_number', ''))
            flat.setdefault('user_name', getattr(order, 'customer_name', '') or 'there')
            flat.setdefault('customer_name', getattr(order, 'customer_name', '') or 'there')
            flat.setdefault('total_amount', getattr(order, 'total_amount', ''))
            flat.setdefault('payment_status', getattr(order, 'payment_status', ''))
            flat.setdefault('order_status', getattr(order, 'status', ''))
            flat.setdefault('discount_amount', getattr(order, 'discount_amount', '') or getattr(order, 'coupon_discount_amount', ''))
            flat.setdefault('coupon_code', getattr(order, 'coupon_code', ''))
            flat.setdefault('delivery_fee', getattr(order, 'delivery_fee', ''))
        elif hasattr(value, 'pk') and key == 'refund':
            flat.setdefault('refund_amount', getattr(value, 'amount', ''))
            flat.setdefault('refund_status', getattr(value, 'status', ''))
        elif hasattr(value, 'pk') and key == 'product':
            flat.setdefault('product_name', getattr(value, 'name', ''))
            flat.setdefault('product_id', getattr(value, 'pk', ''))
            flat.setdefault('available_quantity', getattr(value, 'available_quantity', ''))
            flat.setdefault('threshold', getattr(value, 'low_stock_threshold', ''))
        elif hasattr(value, 'pk') and key == 'review':
            product = getattr(value, 'product', None)
            flat.setdefault('product_name', getattr(product, 'name', '') if product else '')
            comment = (getattr(value, 'admin_comment', '') or '').strip()
            flat.setdefault('admin_comment_line', f' Comment: {comment}' if comment else '')
        elif not hasattr(value, '__dict__') or isinstance(value, (str, int, float, bool)) or value is None:
            flat[key] = '' if value is None else value
        else:
            flat[key] = str(value)
    # Pass through already-flat string keys
    for key, value in ctx.items():
        if key not in flat and isinstance(value, (str, int, float, bool)):
            flat[key] = value
    return flat

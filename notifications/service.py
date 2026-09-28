"""Central NotificationService: Event → channels (email wraps existing helpers)."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from . import events as E
from .channels import get_adapter
from .defaults import DEFAULT_CHANNEL_MAP, DEFAULT_EMAIL_TEMPLATES

logger = logging.getLogger(__name__)
User = get_user_model()


# Preference attribute mapping for non-critical events.
_PREF_EMAIL_ORDER = frozenset({
    E.ORDER_CREATED, E.ORDER_CANCELLED, E.ORDER_STATUS_CHANGED,
    E.REFUND_REQUESTED, E.REFUND_PROCESSING, E.REFUND_COMPLETED, E.REFUND_FAILED,
    E.PAYMENT_INITIATED, E.PAYMENT_CANCELLED,
})
_PREF_EMAIL_DELIVERY = frozenset({
    E.DELIVERY_ASSIGNED, E.ORDER_READY_FOR_DELIVERY,
    E.ORDER_OUT_FOR_DELIVERY, E.DELIVERY_COMPLETED,
})
_PREF_EMAIL_REVIEW = frozenset({E.REVIEW_APPROVED, E.REVIEW_REJECTED, E.REVIEW_SUBMITTED})
_PREF_SMS_ORDER = frozenset(_PREF_EMAIL_ORDER | _PREF_EMAIL_DELIVERY)

_ADMIN_PREF = {
    E.NEW_ORDER: 'admin_alert_new_order',
    E.LOW_STOCK: 'admin_alert_low_stock',
    E.ADMIN_PAYMENT_FAILED: 'admin_alert_payment_failed',
    E.ADMIN_REFUND_FAILED: 'admin_alert_refund_failed',
}


def _get_or_create_prefs(user):
    from .models import NotificationPreference
    if user is None:
        return None
    prefs, _ = NotificationPreference.objects.get_or_create(user=user)
    return prefs


def _preference_allows(event, channel, user) -> bool:
    if event in E.CRITICAL_EVENTS:
        return True
    prefs = _get_or_create_prefs(user)
    if prefs is None:
        return True
    if event in E.ADMIN_EVENTS:
        attr = _ADMIN_PREF.get(event)
        if attr:
            return bool(getattr(prefs, attr, True))
        return True
    if channel == E.CHANNEL_EMAIL:
        if event in _PREF_EMAIL_ORDER:
            return prefs.email_order_updates
        if event in _PREF_EMAIL_DELIVERY:
            return prefs.email_delivery_updates
        if event in _PREF_EMAIL_REVIEW:
            return prefs.email_review_updates
        # promotional / unknown non-critical
        if 'PROMO' in event or event.endswith('_PROMOTIONAL'):
            return prefs.email_promotional
        return True
    if channel == E.CHANNEL_SMS:
        if event in _PREF_SMS_ORDER:
            return prefs.sms_order_updates
        return prefs.sms_promotional or prefs.sms_order_updates
    return True


def _enabled_channels(event, channels_override=None):
    from .models import NotificationChannelConfig

    if channels_override is not None:
        return list(channels_override)

    rows = list(NotificationChannelConfig.objects.filter(event=event, is_enabled=True))
    if rows:
        return [r.channel for r in rows]

    # Fallback to code defaults when DB not seeded yet.
    return [ch for (ev, ch), on in DEFAULT_CHANNEL_MAP.items() if ev == event and on]


def _load_template(event, channel):
    from .models import NotificationTemplate

    row = NotificationTemplate.objects.filter(event=event, channel=channel, is_active=True).first()
    if row:
        return {
            'subject': row.subject or '',
            'body_html': row.body_html or '',
            'body_text': row.body_text or '',
        }
    if channel == E.CHANNEL_EMAIL and event in DEFAULT_EMAIL_TEMPLATES:
        return DEFAULT_EMAIL_TEMPLATES[event]
    # Sensible in-app defaults
    if channel == E.CHANNEL_IN_APP:
        return {
            'subject': event.replace('_', ' ').title(),
            'body_html': '',
            'body_text': '{{message}}' if False else event.replace('_', ' ').title(),
        }
    return {'subject': '', 'body_html': '', 'body_text': ''}


def _mask_recipient(value: str) -> str:
    value = (value or '').strip()
    if not value:
        return ''
    if '@' in value:
        name, domain = value.split('@', 1)
        if len(name) <= 2:
            return f'{name[:1]}***@{domain}'
        return f'{name[:2]}***@{domain}'
    if len(value) <= 4:
        return '***'
    return f'{value[:2]}***{value[-2:]}'


def _already_sent(idempotency_key: str) -> bool:
    from .models import NotificationLog
    if not idempotency_key:
        return False
    return NotificationLog.objects.filter(
        idempotency_key=idempotency_key,
        status=E.STATUS_SENT,
    ).exists()


def _create_log(**kwargs):
    from .models import NotificationLog
    try:
        return NotificationLog.objects.create(**kwargs)
    except IntegrityError:
        # Race on unique idempotency_key
        existing = NotificationLog.objects.filter(idempotency_key=kwargs.get('idempotency_key') or '').first()
        return existing


def _staff_users():
    return list(User.objects.filter(is_staff=True, is_active=True))


def notify(
    event,
    *,
    user=None,
    email=None,
    phone=None,
    context=None,
    channels=None,
    force=False,
    idempotency_key='',
    reference_type='',
    reference_id='',
    admin=False,
):
    """
    Dispatch a notification event to configured channel adapters.

    Returns list of per-channel result dicts.
    """
    context = dict(context or {})
    if reference_type:
        context.setdefault('reference_type', reference_type)
    if reference_id:
        context.setdefault('reference_id', str(reference_id))

    results = []

    # Admin fan-out: notify all staff (or provided user list via context['admin_users'])
    targets = []
    if admin or event in E.ADMIN_EVENTS:
        admin_users = context.pop('admin_users', None) or _staff_users()
        for au in admin_users:
            targets.append({'user': au, 'email': getattr(au, 'email', None), 'phone': None})
        if user is not None and event not in E.ADMIN_EVENTS:
            targets.append({'user': user, 'email': email, 'phone': phone})
    else:
        targets.append({'user': user, 'email': email, 'phone': phone})

    if not targets:
        targets.append({'user': user, 'email': email, 'phone': phone})

    channel_list = _enabled_channels(event, channels)

    for target in targets:
        t_user = target['user']
        t_email = target['email']
        t_phone = target['phone']

        for channel in channel_list:
            base_key = idempotency_key
            if base_key and t_user is not None:
                per_key = f'{base_key}:{channel}:u{t_user.pk}'
            elif base_key:
                per_key = f'{base_key}:{channel}'
            else:
                per_key = ''

            if per_key and _already_sent(per_key) and not force:
                results.append({'event': event, 'channel': channel, 'status': E.STATUS_SKIPPED, 'reason': 'idempotent'})
                _create_log(
                    user=t_user,
                    event=event,
                    channel=channel,
                    status=E.STATUS_SKIPPED,
                    idempotency_key=f'{per_key}:skip',
                    reference_type=reference_type or context.get('reference_type', ''),
                    reference_id=str(reference_id or context.get('reference_id', '')),
                    recipient=_mask_recipient(t_email or ''),
                    last_error='idempotent_already_sent',
                )
                continue

            if not force and not _preference_allows(event, channel, t_user):
                results.append({'event': event, 'channel': channel, 'status': E.STATUS_SKIPPED, 'reason': 'preference'})
                _create_log(
                    user=t_user,
                    event=event,
                    channel=channel,
                    status=E.STATUS_SKIPPED,
                    idempotency_key=per_key or '',
                    reference_type=reference_type or context.get('reference_type', ''),
                    reference_id=str(reference_id or context.get('reference_id', '')),
                    recipient=_mask_recipient(t_email or ''),
                    last_error='preference_opt_out',
                )
                continue

            adapter = get_adapter(channel)
            if adapter is None:
                results.append({'event': event, 'channel': channel, 'status': E.STATUS_SKIPPED, 'reason': 'no_adapter'})
                continue

            tpl = _load_template(event, channel)
            # Enrich in-app body from context message if present
            if channel == E.CHANNEL_IN_APP and context.get('message'):
                tpl = dict(tpl)
                tpl['body_text'] = str(context['message'])
                if context.get('title'):
                    tpl['subject'] = str(context['title'])

            log = _create_log(
                user=t_user,
                event=event,
                channel=channel,
                status=E.STATUS_PENDING,
                idempotency_key=per_key or '',
                reference_type=reference_type or context.get('reference_type', ''),
                reference_id=str(reference_id or context.get('reference_id', '')),
                recipient=_mask_recipient(t_email or phone or ''),
            )

            result = adapter.send(
                event=event,
                user=t_user,
                email=t_email,
                phone=t_phone,
                context=context,
                subject=tpl.get('subject', ''),
                body_html=tpl.get('body_html', ''),
                body_text=tpl.get('body_text', ''),
            )

            # One immediate retry on hard failure (not skip).
            if not result.get('ok') and not result.get('skipped'):
                if log:
                    log.retry_count = 1
                    log.last_error = (result.get('error') or '')[:500]
                    log.save(update_fields=['retry_count', 'last_error'])
                result = adapter.send(
                    event=event,
                    user=t_user,
                    email=t_email,
                    phone=t_phone,
                    context=context,
                    subject=tpl.get('subject', ''),
                    body_html=tpl.get('body_html', ''),
                    body_text=tpl.get('body_text', ''),
                )

            if log is None:
                results.append({'event': event, 'channel': channel, 'status': 'unknown', 'result': result})
                continue

            if result.get('skipped'):
                log.status = E.STATUS_SKIPPED
                log.last_error = (result.get('error') or 'skipped')[:500]
            elif result.get('ok'):
                log.status = E.STATUS_SENT
                log.last_error = ''
                if result.get('recipient'):
                    log.recipient = _mask_recipient(result['recipient'])
            else:
                log.status = E.STATUS_FAILED
                log.last_error = (result.get('error') or 'send_failed')[:500]
            log.save(update_fields=['status', 'last_error', 'recipient', 'retry_count'])

            results.append({
                'event': event,
                'channel': channel,
                'status': log.status,
                'error': log.last_error,
                'user_id': getattr(t_user, 'pk', None),
            })

    return results


# Convenience wrappers used by call sites
def notify_order_confirmed(order):
    user = getattr(order, 'user', None)
    return notify(
        E.ORDER_CONFIRMED,
        user=user,
        email=getattr(order, 'customer_email', None),
        context={'order': order, 'message': f'Order {order.order_number} confirmed.'},
        idempotency_key=f'order_confirmed:{order.pk}',
        reference_type='order',
        reference_id=str(order.pk),
    )


def notify_payment_success(order, payment=None):
    user = getattr(order, 'user', None)
    key = f'payment_success:{getattr(payment, "pk", order.pk)}'
    return notify(
        E.PAYMENT_SUCCESS,
        user=user,
        email=getattr(order, 'customer_email', None),
        context={'order': order, 'payment': payment, 'message': f'Payment successful for {order.order_number}.'},
        idempotency_key=key,
        reference_type='payment',
        reference_id=str(getattr(payment, 'pk', '') or order.pk),
    )


def notify_order_cancelled(order, reason=''):
    user = getattr(order, 'user', None)
    paid = getattr(order, 'payment_status', '') == 'paid'
    msg = (
        f'Order {order.order_number} cancelled. If payment was captured, a refund will be processed separately.'
        if paid else
        f'Order {order.order_number} cancelled.'
    )
    return notify(
        E.ORDER_CANCELLED,
        user=user,
        email=getattr(order, 'customer_email', None),
        context={'order': order, 'reason': reason, 'message': msg},
        idempotency_key=f'order_cancelled:{order.pk}',
        reference_type='order',
        reference_id=str(order.pk),
    )


def notify_staff_new_order(order):
    return notify(
        E.NEW_ORDER,
        context={
            'order': order,
            'title': f'New order {order.order_number}',
            'message': f'New order {order.order_number} from {getattr(order, "customer_name", "")} — Rs.{getattr(order, "total_amount", "")}',
        },
        idempotency_key=f'new_order:{order.pk}',
        reference_type='order',
        reference_id=str(order.pk),
        admin=True,
    )


def notify_low_stock(product, previous_availability=''):
    return notify(
        E.LOW_STOCK,
        context={
            'product': product,
            'previous_availability': previous_availability,
            'title': f'Low stock: {getattr(product, "name", "")}',
            'message': (
                f'{getattr(product, "name", "")} is low on stock '
                f'(available={getattr(product, "available_quantity", "")}, '
                f'threshold={getattr(product, "low_stock_threshold", "")}).'
            ),
            'available_quantity': getattr(product, 'available_quantity', ''),
            'threshold': getattr(product, 'low_stock_threshold', ''),
            'product_name': getattr(product, 'name', ''),
            'product_id': getattr(product, 'pk', ''),
        },
        idempotency_key=f'low_stock:{product.pk}:{getattr(product, "available_quantity", "")}',
        reference_type='product',
        reference_id=str(product.pk),
        admin=True,
    )


def tracking_url_for_order(order):
    base = getattr(settings, 'FRONTEND_URL', 'https://pinkbakes.com').rstrip('/')
    return f'{base}/orders/{order.pk}/tracking'

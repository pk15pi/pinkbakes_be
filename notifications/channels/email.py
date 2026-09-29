from __future__ import annotations

import logging

from django.conf import settings

from accounts.email_service import (
    build_otp_email_html,
    build_password_reset_email_html,
    build_verification_email_html,
    build_verification_email_html_with_otp,
    send_html_email,
    send_order_cancellation_email,
    send_order_confirmation_email,
    send_refund_completed_email,
    send_refund_failed_email,
    send_refund_initiated_email,
)
from notifications import events as E
from notifications.rendering import flatten_context, render_template

logger = logging.getLogger(__name__)


class EmailChannelAdapter:
    channel = E.CHANNEL_EMAIL

    def is_configured(self) -> bool:
        if getattr(settings, 'NOTIFICATION_EMAIL_ENABLED', True) is False:
            return False
        return True

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        if not self.is_configured():
            return {'ok': False, 'skipped': True, 'error': 'email_disabled'}

        context = context or {}
        recipient = (email or '').strip()
        if not recipient and user is not None:
            recipient = (getattr(user, 'email', None) or '').strip()
        if not recipient and context.get('order') is not None:
            order = context['order']
            recipient = (getattr(order, 'customer_email', None) or '').strip()
            if not recipient:
                ou = getattr(order, 'user', None)
                recipient = (getattr(ou, 'email', None) or '').strip() if ou else ''

        if not recipient:
            return {'ok': False, 'skipped': True, 'error': 'no_recipient'}

        # Prefer dedicated helpers for existing transactional emails (preserves content).
        try:
            if event == E.ORDER_CONFIRMED and context.get('order') is not None:
                send_order_confirmation_email(context['order'])
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.ORDER_CANCELLED and context.get('order') is not None:
                reason = context.get('reason') or getattr(context['order'], 'cancellation_reason', '') or ''
                send_order_cancellation_email(context['order'], reason=reason)
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.REFUND_REQUESTED and context.get('order') is not None and context.get('refund') is not None:
                send_refund_initiated_email(context['order'], context['refund'])
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.REFUND_COMPLETED and context.get('order') is not None and context.get('refund') is not None:
                send_refund_completed_email(context['order'], context['refund'])
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.REFUND_FAILED and context.get('order') is not None and context.get('refund') is not None:
                send_refund_failed_email(context['order'], context['refund'])
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.EMAIL_VERIFICATION_REQUIRED:
                url = context.get('verification_url') or ''
                otp = context.get('otp') or ''
                name = context.get('user_name') or (getattr(user, 'first_name', None) or getattr(user, 'username', '') if user else 'there')
                html = body_html or build_verification_email_html_with_otp(name, url, otp)
                subj = subject or 'Verify Your PinkBakes Account'
                text = body_text or (f'Verify: {url}' + (f' OTP: {otp}' if otp else ''))
                send_html_email(subj, [recipient], html, text_content=text)
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.MOBILE_VERIFICATION_REQUIRED:
                url = context.get('verification_url') or ''
                otp = context.get('otp') or ''
                name = context.get('user_name') or (getattr(user, 'first_name', None) or getattr(user, 'username', '') if user else 'there')
                html = body_html or build_verification_email_html_with_otp(name, url, otp)
                subj = subject or 'Verify Your PinkBakes Account'
                text = body_text or (f'Verify: {url}' + (f' OTP: {otp}' if otp else ''))
                send_html_email(subj, [recipient], html, text_content=text)
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.LOGIN_OTP_REQUESTED:
                otp = context.get('otp') or ''
                if not otp:
                    return {'ok': False, 'skipped': True, 'error': 'missing_otp'}
                name = context.get('user_name') or (getattr(user, 'first_name', None) or getattr(user, 'username', '') if user else 'there')
                html = body_html or build_otp_email_html(name, otp, purpose='login')
                subj = subject or 'Your PinkBakes login code'
                text = body_text or f'Your PinkBakes login OTP is {otp}. It expires in 10 minutes.'
                send_html_email(subj, [recipient], html, text_content=text)
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
            if event == E.PASSWORD_RESET_REQUESTED:
                url = context.get('reset_url') or ''
                name = context.get('user_name') or (getattr(user, 'first_name', None) or getattr(user, 'username', '') if user else 'there')
                html = body_html or build_password_reset_email_html(name, url)
                subj = subject or 'Reset Your PinkBakes Password'
                send_html_email(subj, [recipient], html, text_content=body_text or None)
                return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}

            # Generic template path
            flat = flatten_context(context)
            if user is not None:
                flat.setdefault('user_name', getattr(user, 'first_name', None) or getattr(user, 'username', '') or 'there')
            html = render_template(body_html, flat) if body_html else ''
            text = render_template(body_text, flat) if body_text else None
            subj = render_template(subject, flat) if subject else f'PinkBakes — {event}'
            if not html and not text:
                return {'ok': False, 'skipped': True, 'error': 'no_template'}
            send_html_email(subj, [recipient], html or f'<p>{text}</p>', text_content=text)
            return {'ok': True, 'skipped': False, 'error': '', 'recipient': recipient}
        except Exception as exc:
            logger.exception('Email channel failed for %s', event)
            return {'ok': False, 'skipped': False, 'error': str(exc)[:500], 'recipient': recipient}

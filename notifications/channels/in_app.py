from __future__ import annotations

from notifications import events as E
from notifications.rendering import flatten_context, render_template


class InAppChannelAdapter:
    channel = E.CHANNEL_IN_APP

    def is_configured(self) -> bool:
        return True

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        if user is None:
            return {'ok': False, 'skipped': True, 'error': 'no_user'}
        from notifications.models import InAppNotification

        flat = flatten_context(context)
        title = render_template(subject, flat) if subject else event.replace('_', ' ').title()
        body = render_template(body_text or body_html, flat) if (body_text or body_html) else flat.get('message', title)
        # Strip crude HTML tags for in-app body
        if '<' in str(body):
            import re
            body = re.sub(r'<[^>]+>', '', str(body))
        ref_type = (context or {}).get('reference_type') or ''
        ref_id = str((context or {}).get('reference_id') or '')
        if not ref_type and (context or {}).get('order') is not None:
            ref_type = 'order'
            ref_id = str(getattr(context['order'], 'pk', ''))
        InAppNotification.objects.create(
            user=user,
            event=event,
            title=str(title)[:200],
            body=str(body)[:4000],
            reference_type=str(ref_type)[:40],
            reference_id=str(ref_id)[:64],
        )
        return {'ok': True, 'skipped': False, 'error': ''}

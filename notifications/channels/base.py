from __future__ import annotations


class ChannelAdapter:
    channel = 'base'

    def is_configured(self) -> bool:
        return True

    def send(self, *, event, user=None, email=None, phone=None, context=None, subject='', body_html='', body_text='') -> dict:
        """
        Return dict: {ok: bool, skipped: bool, error: str}
        """
        raise NotImplementedError

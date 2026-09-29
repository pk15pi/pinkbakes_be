"""Scrub passwords, OTPs, and tokens from log records."""

from __future__ import annotations

import logging
import re

_SENSITIVE_PATTERNS = [
    re.compile(r'(?i)(password["\']?\s*[:=]\s*)([^\s,;]+)'),
    re.compile(r'(?i)(otp["\']?\s*[:=]\s*)([0-9]{4,8})'),
    re.compile(r'(?i)(token["\']?\s*[:=]\s*)([A-Za-z0-9_\-\.]+)'),
    re.compile(r'(?i)(authorization:\s*token\s+)([A-Za-z0-9]+)'),
    re.compile(r'(?i)(razorpay[_-]?key[_-]?secret["\']?\s*[:=]\s*)([^\s,;]+)'),
    re.compile(r'(?i)(webhook[_-]?secret["\']?\s*[:=]\s*)([^\s,;]+)'),
]


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        scrubbed = msg
        for pattern in _SENSITIVE_PATTERNS:
            scrubbed = pattern.sub(r'\1***', scrubbed)
        if scrubbed != msg:
            record.msg = scrubbed
            record.args = ()
        return True

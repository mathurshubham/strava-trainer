"""Regex-redact known secret shapes before they ever reach a log line or a user-facing message.

PRD §8.1: "Never log a secret. Add a logging filter that regex-redacts anything
matching known token shapes." This is defense in depth — the real control is
"never pass a secret to logging.info in the first place" — but mistakes happen.
"""
from __future__ import annotations

import logging
import re

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d{8,10}:AA[\w-]{25,40}\b"),  # Telegram bot token
    re.compile(r"\bAIza[\w-]{30,40}\b"),  # Google API key
    re.compile(r"\b[0-9a-f]{40}\b"),  # Strava client secret / 40-char hex tokens
    re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),  # JWT-shaped
]

_REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """Attach to every handler: `handler.addFilter(RedactingFilter())`."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(str(a)) if isinstance(a, str) else a for a in record.args)
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    root.handlers = [handler]

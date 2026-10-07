"""Redaction of secrets and PII (Architecture Spec Sections 9.1, 9.2).

Phase 1 scope: secrets and email addresses, which the logger needs from day one.
Phase 3 adds the remaining patterns from Section 9.2 (names, phone, account and
card numbers, national IDs, IP and street addresses, Kafka principals) and the
consistent pseudonym tokens, behind the same `redact` interface.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

# (flag, pattern, replacement). Order matters: specific patterns run first.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("secret_pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED_PRIVATE_KEY]"),
    ("secret_api_key", re.compile(r"\b(?:sk|voc|pk-lf|sk-lf|rk)-[A-Za-z0-9_\-]{16,}"), "[REDACTED_KEY]"),
    ("secret_bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED_TOKEN]"),
    ("secret_url_credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s@/]+@"),
     r"\1[REDACTED_CREDENTIALS]@"),
    ("secret_assignment",
     re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|token)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"),
     r"\1\2[REDACTED]"),
    ("pii_email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
]

_extra_secret_provider: Callable[[], Iterable[str]] | None = None


def register_secret_provider(provider: Callable[[], Iterable[str]]) -> None:
    """Register a function returning exact secret values (for example the
    configured API keys) that must always be scrubbed, whatever their format."""
    global _extra_secret_provider
    _extra_secret_provider = provider


def redact(text: str) -> tuple[str, list[str]]:
    """Return the text with secrets and PII replaced, and the flags that fired."""
    if not text:
        return text, []
    flags: list[str] = []
    if _extra_secret_provider is not None:
        try:
            for value in _extra_secret_provider():
                if value and len(value) >= 8 and value in text:
                    text = text.replace(value, "[REDACTED_KEY]")
                    flags.append("secret_configured")
        except Exception:  # noqa: BLE001 - redaction must never fail the caller
            pass
    for flag, pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        if count:
            flags.append(flag)
    return text, flags


def redact_text(text: str) -> str:
    """Redacted text only."""
    return redact(text)[0]


def redact_value(value: Any) -> Any:
    """Redact every string inside a value (str, dict, list or tuple), recursively."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_value(item) for item in value)
    return value

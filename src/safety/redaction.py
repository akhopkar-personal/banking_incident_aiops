"""Redaction of secrets and PII (Architecture Spec Sections 9.1, 9.2; Req. 13.3).

Secrets become fixed placeholders (`[REDACTED_KEY]`). Personal data becomes a
consistent pseudonym token such as `ACCT_7F3A`: the same value always gets the
same token within one process, so evidence stays linkable without exposing the
value. The token map lives in memory only and is never written anywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
from typing import Any, Callable, Iterable

from src.safety.pii_names import SYNTHETIC_NAMES

# ------------------------------------------------------------- pseudonyms

# One key per session: created by the first process and inherited by the processes it starts
# (the MCP server), so a value gets the same token on both sides of the MCP boundary (Req. FR-73).
# It lives only in process memory and the environment of child processes; it is never written.
PSEUDONYM_KEY_ENV = "AIOPS_PSEUDONYM_KEY"
if not os.environ.get(PSEUDONYM_KEY_ENV):
    os.environ[PSEUDONYM_KEY_ENV] = os.urandom(16).hex()
_PROCESS_KEY = bytes.fromhex(os.environ[PSEUDONYM_KEY_ENV])
_token_lock = threading.Lock()
_tokens: dict[tuple[str, str], str] = {}
_used: set[str] = set()


def pseudonym(kind: str, value: str) -> str:
    """Consistent token for `value`, for example pseudonym('ACCT', '12345678') -> 'ACCT_7F3A'."""
    key = (kind, value)
    with _token_lock:
        token = _tokens.get(key)
        if token is None:
            digest = hmac.new(_PROCESS_KEY, f"{kind}|{value}".encode(), hashlib.sha256).hexdigest().upper()
            length = 4
            token = f"{kind}_{digest[:length]}"
            while token in _used:  # different value, same prefix: lengthen
                length += 1
                token = f"{kind}_{digest[:length]}"
            _tokens[key] = token
            _used.add(token)
        return token


def _luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if double:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        double = not double
    return total % 10 == 0


# --------------------------------------------------------------- patterns

# Digits that are part of an identifier (TXN-20260314-000123, INC-..., v2.14.0, a timestamp)
# are never PII: a match must not touch a word character, '-', '.', ':' or '/'.
_NB = r"(?<![\w\-./:])"
_NA = r"(?![\w\-/:]|\.\d)"

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("secret_pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "[REDACTED_PRIVATE_KEY]"),
    ("secret_api_key", re.compile(r"\b(?:sk|voc|pk-lf|sk-lf|rk)-[A-Za-z0-9_\-]{16,}"), "[REDACTED_KEY]"),
    ("secret_bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED_TOKEN]"),
    ("secret_url_credentials", re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s:/@]+:[^\s@/]+@"),
     r"\1[REDACTED_CREDENTIALS]@"),
    ("secret_assignment",
     re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|token)(\s*[=:]\s*)"
                r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"),
     r"\1\2[REDACTED]"),
]

_NAME_LIST = re.compile("|".join(re.escape(n) for n in sorted(SYNTHETIC_NAMES, key=len, reverse=True)))
# "My name is Jane Doe": the capitalized words after the phrase, whatever they are.
# Only the phrase is case-insensitive; the name must be capitalized.
_NAME_PHRASE = re.compile(r"\b((?i:my name is|i am called))\s+([A-Z][a-z'\u2019]+(?:\s+[A-Z][a-z'\u2019]+){0,2})")


def _sub_token(kind: str) -> Callable[[re.Match[str]], str]:
    return lambda m: pseudonym(kind, m.group(0))


def _card(m: re.Match[str]) -> str:
    digits = re.sub(r"[ \-]", "", m.group(0))
    return pseudonym("CARD", digits) if _luhn_ok(digits) else m.group(0)


# (flag, pattern, replacement). Order matters: earlier patterns run first.
_PII_PATTERNS: list[tuple[str, re.Pattern[str], Any]] = [
    ("pii_email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), _sub_token("EMAIL")),
    ("pii_principal", re.compile(r"\bUser:([A-Za-z0-9._\-]+)"), lambda m: "User:" + pseudonym("PRINCIPAL", m.group(1))),
    ("pii_service_account", re.compile(r"\bsvc-[a-z0-9][a-z0-9\-]*\b"), _sub_token("SVCACCT")),
    ("pii_card", re.compile(_NB + r"(?:\d[ \-]?){12,18}\d" + _NA), _card),
    ("pii_phone", re.compile(_NB + r"(?:\+44\s?7\d{3}|07\d{3})\s?\d{3}\s?\d{3}" + _NA), _sub_token("PHONE")),
    ("pii_phone", re.compile(_NB + r"\+\d{1,3}[\s\-]\d{2,4}[\s\-]\d{3,4}[\s\-]\d{3,4}" + _NA), _sub_token("PHONE")),
    ("pii_account", re.compile(_NB + r"\d{8,16}" + _NA), _sub_token("ACCT")),
    ("pii_national_id", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"), _sub_token("NATID")),
    ("pii_ip", re.compile(_NB + r"(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)" + _NA),
     _sub_token("IP")),
    ("pii_address", re.compile(r"\b\d{1,4}\s+(?:[A-Z][a-z]+\s+){1,3}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|"
                               r"Close|Drive|Dr|Way|Court|Ct)\b"), _sub_token("ADDR")),
    ("pii_name", _NAME_LIST, _sub_token("PERSON")),
    ("pii_name", _NAME_PHRASE, lambda m: f"{m.group(1)} {pseudonym('PERSON', m.group(2))}"),
]

# Capitalized word pairs that are not names, for the complaint-text heuristic.
_NOT_NAMES = {
    "Fund", "Transfer", "Card", "Payment", "Payments", "Mobile", "App", "Net", "Banking", "Bank", "Customer",
    "Service", "Support", "Schema", "Registry", "Kafka", "Connect", "Incident", "Escalation", "Production",
    "Please", "The", "This", "My", "Your", "Our", "Why", "What", "When", "How", "Is", "I", "Also", "Same",
    "Double", "Online", "Monthly", "Balance", "Login", "Utility", "Statement", "Very", "Cannot", "No", "AI",
    "Tried", "Transfer", "Money", "Not", "Thanks", "Thank", "You", "UPI", "NEFT", "SMS", "UK",
}
_CAP_PAIR = re.compile(r"\b([A-Z][a-z'\u2019]{1,20})\s+([A-Z][a-z'\u2019]{1,20})\b")

_extra_secret_provider: Callable[[], Iterable[str]] | None = None


def register_secret_provider(provider: Callable[[], Iterable[str]]) -> None:
    """Register a function returning exact secret values (for example the
    configured API keys) that must always be scrubbed, whatever their format."""
    global _extra_secret_provider
    _extra_secret_provider = provider


def redact(text: str) -> tuple[str, list[str]]:
    """Return the text with secrets and PII replaced, and the flags that fired (each once)."""
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
    for flag, pattern, replacement in _SECRET_PATTERNS + _PII_PATTERNS:
        new_text = pattern.sub(replacement, text)
        if new_text != text and flag not in flags:  # a match can be left as is (a non-Luhn "card")
            flags.append(flag)
        text = new_text
    return text, flags


def redact_complaint(text: str) -> tuple[str, list[str]]:
    """Complaint text: `redact` plus a capitalized-pair name heuristic (Section 9.2),
    which is too aggressive for technical text and so is used for complaints only."""
    text, flags = redact(text)

    def replace(m: re.Match[str]) -> str:
        first, last = m.group(1), m.group(2)
        if first in _NOT_NAMES or last in _NOT_NAMES:
            return m.group(0)
        if "pii_name" not in flags:
            flags.append("pii_name")
        return pseudonym("PERSON", m.group(0))

    return _CAP_PAIR.sub(replace, text), flags


def redact_text(text: str) -> str:
    """Redacted text only."""
    return redact(text)[0]


def redact_value(value: Any) -> Any:
    """Redact every string inside a value (str, dict, list or tuple), recursively."""
    return redact_record(value)[0]


def redact_record(value: Any) -> tuple[Any, list[str]]:
    """Redact every string inside a value, and return the union of flags."""
    flags: list[str] = []

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            text, found = redact(item)
            flags.extend(f for f in found if f not in flags)
            return text
        if isinstance(item, dict):
            return {key: walk(sub) for key, sub in item.items()}
        if isinstance(item, (list, tuple)):
            return type(item)(walk(sub) for sub in item)
        return item

    return walk(value), flags

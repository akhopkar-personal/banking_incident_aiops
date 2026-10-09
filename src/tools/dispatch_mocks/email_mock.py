"""Simulated email (Architecture Spec Section 7.4). Writes data/outbox/notifications.jsonl.

Recipients come from the synthetic stakeholder directory and are kept as
routing data; subject and body are redacted like every other outbox text."""

from __future__ import annotations

from typing import Any, Optional

from . import outbox


def send_email(to: list[str], subject: str, body: str, incident_id: Optional[str] = None, audience: str = "",
               severity: str = "") -> dict[str, Any]:
    return outbox.append("notifications", incident_id,
                         {"channel": "email", "to": list(to), "audience": audience, "severity": severity,
                          "subject": subject, "body": body}, prefix="MAIL", keep_plain=("to",))

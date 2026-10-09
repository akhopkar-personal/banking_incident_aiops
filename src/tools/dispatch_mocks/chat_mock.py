"""Simulated chat (Architecture Spec Section 7.4). Writes data/outbox/notifications.jsonl."""

from __future__ import annotations

from typing import Any, Optional

from . import outbox


def send_chat_alert(channel: str, text: str, incident_id: Optional[str] = None, audience: str = "",
                    severity: str = "") -> dict[str, Any]:
    return outbox.append("notifications", incident_id,
                         {"channel": "chat", "destination": channel, "audience": audience, "severity": severity,
                          "text": text}, prefix="CHAT", keep_plain=("destination",))

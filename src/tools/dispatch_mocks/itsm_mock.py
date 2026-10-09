"""Simulated ITSM (Architecture Spec Section 7.4; Req. FR-44). Writes
data/outbox/itsm.jsonl. Upserts are idempotent on the incident's idempotency key."""

from __future__ import annotations

import threading
from typing import Any, Optional

from . import outbox

_lock = threading.Lock()


def find_ticket(idempotency_key: str) -> Optional[str]:
    for line in outbox.read("itsm"):
        if line.get("operation") == "upsert" and line.get("idempotency_key") == idempotency_key:
            return line["ticket_id"]
    return None


def itsm_upsert_incident(idempotency_key: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Create a ticket, or update the existing one for the same key. Returns {ticket_id, created, ...}."""
    with _lock:
        existing = find_ticket(idempotency_key)
        if existing:
            ticket_id, created = existing, False
        else:
            count = sum(1 for line in outbox.read("itsm") if line.get("operation") == "upsert" and line.get("created"))
            ticket_id, created = f"MOCK-ITSM-{count + 1:04d}", True
        line = outbox.append("itsm", fields.get("incident_id"),
                             {"operation": "upsert", "ticket_id": ticket_id, "created": created,
                              "idempotency_key": idempotency_key, "fields": fields},
                             prefix="ITSM", keep_plain=("ticket_id", "idempotency_key"))
    return line


def itsm_assign(ticket_id: str, queue: str, summary: str, fix: str, incident_id: Optional[str] = None
                ) -> dict[str, Any]:
    return outbox.append("itsm", incident_id, {"operation": "assign", "ticket_id": ticket_id, "queue": queue,
                                               "summary": f"[SIMULATED] {summary}", "recommended_fix": fix},
                         prefix="ITSM", keep_plain=("ticket_id",))

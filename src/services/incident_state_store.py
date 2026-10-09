"""Incident State Store (Architecture Spec Section 5.6; Req. FR-39).

Append-only JSON lines in data/incident_state/incidents.jsonl; the current
state of an incident is the fold of its records. Final outputs of each run are
kept in outputs/<run_id>.json so human steps can load them after the graph run.
"""

from __future__ import annotations

import itertools
import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from src import config
from src.safety.redaction import redact_record
from src.schemas.enums import IncidentState
from src.schemas.feedback import IncidentStateRecord
from src.schemas.output import InvestigationOutput

_lock = threading.RLock()
_run_counter = itertools.count(1)
# Identifiers used for lookups are not personal data and must not be pseudonymized.
_PLAIN_KEYS = ("idempotency_key", "ticket_id", "bucket_start", "outbox_id", "queue", "mode", "status",
               "content_hash", "doc_id", "feedback_id", "candidate_id")


def _dir() -> Path:
    return config.get_settings().incident_state_dir


def _file() -> Path:
    return _dir() / "incidents.jsonl"


def new_run_id() -> str:
    """RUN-<milliseconds since epoch><two-digit process counter>: unique and sortable."""
    return f"RUN-{int(time.time() * 1000)}{next(_run_counter) % 100:02d}"


def now() -> datetime:
    return datetime.now(timezone.utc)


def records(incident_id: Optional[str] = None) -> list[IncidentStateRecord]:
    path = _file()
    if not path.exists():
        return []
    with _lock:
        lines = path.read_text(encoding="utf-8").splitlines()
    out = [IncidentStateRecord.model_validate_json(line) for line in lines if line.strip()]
    return [r for r in out if incident_id is None or r.incident_id == incident_id]


def append(incident_id: str, run_id: str, event: str, state: IncidentState, actor: str,
           payload: Optional[dict[str, Any]] = None, at: Optional[datetime] = None) -> IncidentStateRecord:
    """Append one lifecycle record (payload redacted)."""
    with _lock:
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        count = sum(1 for _ in open(path, encoding="utf-8")) if path.exists() else 0
        payload = dict(payload or {})
        plain = {k: payload.pop(k) for k in _PLAIN_KEYS if k in payload}
        clean, _ = redact_record(payload)
        clean.update(plain)
        record = IncidentStateRecord(record_id=f"ISR-{count + 1:06d}", incident_id=incident_id, run_id=run_id,
                                     event=event, incident_state=state, actor=actor, payload=clean, at=at or now())
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json() + "\n")
        return record


@dataclass
class IncidentView:
    incident_id: str
    state: IncidentState
    created_at: datetime
    idempotency_key: Optional[str] = None
    root_service: Optional[str] = None
    scenario_id: Optional[str] = None
    run_ids: list[str] = field(default_factory=list)
    ticket_id: Optional[str] = None
    paged_at: list[datetime] = field(default_factory=list)
    assigned_queue: Optional[str] = None
    review_status: Optional[str] = None
    resolution: Optional[dict[str, Any]] = None
    verification: Optional[dict[str, Any]] = None
    events: list[str] = field(default_factory=list)
    # v1.9: human steps (Req. 13.5)
    acknowledged_at: Optional[datetime] = None
    reviews: dict[str, dict[str, Any]] = field(default_factory=dict)  # run_id -> reviewed payload
    reclassifications: list[dict[str, Any]] = field(default_factory=list)
    gate_handoffs: dict[str, dict[str, Any]] = field(default_factory=dict)  # run_id -> payload
    indexed_doc_id: Optional[str] = None


def _fold(items: list[IncidentStateRecord]) -> Optional[IncidentView]:
    if not items:
        return None
    first = items[0]
    view = IncidentView(incident_id=first.incident_id, state=first.incident_state, created_at=first.at)
    for r in items:
        view.state = r.incident_state
        view.events.append(r.event)
        if r.run_id not in view.run_ids:
            view.run_ids.append(r.run_id)
        p = r.payload
        if r.event == "created":
            view.idempotency_key = p.get("idempotency_key")
            view.root_service = p.get("root_service")
            view.scenario_id = p.get("scenario_id")
        elif r.event == "ticket_upserted":
            view.ticket_id = p.get("ticket_id") or view.ticket_id
        elif r.event == "paged" and p.get("mode", "new") == "new":
            view.paged_at.append(r.at)
        elif r.event == "assigned":
            view.assigned_queue = p.get("queue")
        elif r.event == "reviewed":
            view.review_status = p.get("status")
            view.reviews[r.run_id] = {**p, "at": r.at.isoformat(), "actor": r.actor}
        elif r.event == "acknowledged":
            view.acknowledged_at = view.acknowledged_at or r.at
        elif r.event == "reclassified":
            view.reclassifications.append({**p, "at": r.at.isoformat(), "actor": r.actor})
        elif r.event == "gate_handoff":
            view.gate_handoffs[r.run_id] = p
        elif r.event == "indexed":
            view.indexed_doc_id = p.get("doc_id")
        elif r.event == "resolved":
            view.resolution = p
        elif r.event == "verified":
            view.verification = p
    return view


def current(incident_id: str) -> Optional[IncidentView]:
    return _fold(records(incident_id))


def find_by_key(idempotency_key: str, since: Optional[datetime] = None) -> Optional[str]:
    """Incident ID created with this idempotency key (optionally only after `since`)."""
    for r in records():
        if r.event == "created" and r.payload.get("idempotency_key") == idempotency_key and (
                since is None or r.at >= since):
            return r.incident_id
    return None


def list_incidents(state: Optional[IncidentState] = None, root_service: Optional[str] = None,
                   since: Optional[datetime] = None) -> list[IncidentView]:
    grouped: dict[str, list[IncidentStateRecord]] = {}
    for r in records():
        grouped.setdefault(r.incident_id, []).append(r)
    views = [v for v in (_fold(items) for items in grouped.values()) if v is not None]
    return [v for v in views if (state is None or v.state == state)
            and (root_service is None or v.root_service == root_service)
            and (since is None or v.created_at >= since)]


def next_incident_id(on: datetime) -> str:
    """INC-YYYYMMDD-NNN, numbered per date (the incident's reference date)."""
    prefix = f"INC-{on:%Y%m%d}-"
    used = {r.incident_id for r in records() if r.incident_id.startswith(prefix)}
    return f"{prefix}{len(used) + 1:03d}"


def recently_paged(incident_id: str, within_min: int) -> bool:
    view = current(incident_id)
    return bool(view and any(t >= now() - timedelta(minutes=within_min) for t in view.paged_at))


def save_output(output: InvestigationOutput) -> Path:
    path = _dir() / "outputs" / f"{output.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output.to_output_dict(), indent=1), encoding="utf-8")
    return path


def load_output(run_id: str) -> Optional[dict[str, Any]]:
    path = _dir() / "outputs" / f"{run_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_incident(run_id: str) -> Optional[dict[str, Any]]:
    """The run's Incident Object, saved next to its output by the graph's finalize step."""
    path = _dir() / "outputs" / f"{run_id}.incident.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def archive_state() -> Optional[Path]:
    """Move incidents.jsonl and outputs/ to archive/<timestamp>/ (the UI's "reset demo"). Never deletes.
    Incident numbering restarts; feedback records keep their unique run IDs."""
    directory = _dir()
    with _lock:
        present = [p for p in (_file(), directory / "outputs") if p.exists()]
        if not present:
            return None
        target = directory / "archive" / now().strftime("%Y%m%dT%H%M%S%fZ")
        target.mkdir(parents=True, exist_ok=True)
        for path in present:
            shutil.move(str(path), str(target / path.name))
        return target

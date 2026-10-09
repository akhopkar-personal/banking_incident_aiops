"""Preference store (Architecture Spec Section 10.4; Req. FR-60, FR-64).

Append-only JSON lines in data/feedback/preferences.jsonl, one `PreferenceRecord`
per human signal. Free text is PII-checked before it is written. A repeat of the
same signal (same reviewer, run and signal type within 10 minutes) is still kept,
but marked `duplicate_of` the first one so it is counted once.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from src import config
from src.safety.redaction import redact_record
from src.schemas.enums import SignalType
from src.schemas.feedback import PreferenceIntegrity, PreferenceRecord

DUPLICATE_WINDOW = timedelta(minutes=10)
_lock = threading.RLock()


def _file() -> Path:
    return config.get_settings().feedback_dir / "preferences.jsonl"


def new_feedback_id() -> str:
    return f"FB-{uuid.uuid4().hex[:10].upper()}"


def records() -> list[PreferenceRecord]:
    path = _file()
    if not path.exists():
        return []
    with _lock:
        lines = path.read_text(encoding="utf-8").splitlines()
    return [PreferenceRecord.model_validate_json(line) for line in lines if line.strip()]


def _duplicate_of(record: PreferenceRecord, existing: list[PreferenceRecord]) -> Optional[str]:
    for other in existing:
        if (other.reviewer_id == record.reviewer_id and other.run_id == record.run_id
                and other.signal_type == record.signal_type and other.integrity.duplicate_of is None
                and abs(record.created_at - other.created_at) <= DUPLICATE_WINDOW):
            return other.feedback_id
    return None


def append(record: PreferenceRecord) -> PreferenceRecord:
    """PII-check the payload, mark duplicates and append. Returns the record as written."""
    with _lock:
        payload, _ = redact_record(record.payload)
        integrity = PreferenceIntegrity(agreement_status=record.integrity.agreement_status,
                                        duplicate_of=_duplicate_of(record, records()), pii_checked=True)
        written = record.model_copy(update={"payload": payload, "integrity": integrity})
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(written.model_dump_json() + "\n")
        return written


def query(signal_type: Optional[SignalType] = None, *, incident_id: Optional[str] = None,
          run_id: Optional[str] = None, scenario_id: Optional[str] = None,
          prompt_version_set: Optional[dict[str, str]] = None, since: Optional[datetime] = None,
          include_duplicates: bool = False) -> list[PreferenceRecord]:
    def keep(r: PreferenceRecord) -> bool:
        return ((signal_type is None or r.signal_type == signal_type)
                and (incident_id is None or r.incident_id == incident_id)
                and (run_id is None or r.run_id == run_id)
                and (scenario_id is None or r.scenario_id == scenario_id)
                and (prompt_version_set is None or r.prompt_version_set == prompt_version_set)
                and (since is None or r.created_at >= since)
                and (include_duplicates or r.integrity.duplicate_of is None))

    return [r for r in records() if keep(r)]


def make(signal_type: SignalType, context: dict[str, Any], reviewer_role, reviewer_id: str,
         payload: dict[str, Any], at: datetime, agent: Optional[str] = None) -> PreferenceRecord:
    """A new record for one signal. `context` is `review_store.run_context(...)`."""
    return PreferenceRecord(feedback_id=new_feedback_id(), signal_type=signal_type,
                            incident_id=context["incident_id"], run_id=context["run_id"],
                            langfuse_trace_id=context.get("langfuse_trace_id"),
                            scenario_id=context.get("scenario_id"),
                            prompt_version_set=context.get("prompt_version_set") or {}, agent=agent,
                            reviewer_role=reviewer_role, reviewer_id=reviewer_id, payload=payload, created_at=at)


# ------------------------------------------------------- pairwise agreement (FR-64)


def pair_labels(pair_id: str) -> list[PreferenceRecord]:
    return [r for r in query(SignalType.PAIRWISE) if r.payload.get("pair_id") == pair_id]


def pair_outcome(pair_id: str) -> tuple[str, Optional[str]]:
    """(agreement status, final choice A/B/tie or None). Two different reviewers who agree make the pair
    `agreed`; if they disagree it is `disputed` until the SME breaks the tie (`tie_broken`)."""
    labels = pair_labels(pair_id)
    reviewers: dict[str, str] = {}
    sme_choice: Optional[str] = None
    for r in labels:
        if r.reviewer_role.value == "sme" and len(reviewers) >= 2:
            sme_choice = sme_choice or r.payload["choice"]
        else:
            reviewers.setdefault(r.reviewer_id, r.payload["choice"])
    choices = list(reviewers.values())[:2]
    if not choices:
        return "none", None
    if len(choices) == 1:
        return "single", None
    if choices[0] == choices[1]:
        return "agreed", choices[0]
    return ("tie_broken", sme_choice) if sme_choice else ("disputed", None)


def agreement(pair_id: str) -> str:
    """Architecture Spec Section 10.4: `agreed`, `disputed`, `tie_broken`, `single` (or `none`)."""
    return pair_outcome(pair_id)[0]


def reviewer_share(feedback_ids: list[str]) -> dict[str, float]:
    """Share of the given signals per reviewer, for the 30% cap on any one reviewer (FR-64)."""
    wanted = set(feedback_ids)
    owners = [r.reviewer_id for r in records() if r.feedback_id in wanted]
    return {rid: owners.count(rid) / len(owners) for rid in dict.fromkeys(owners)} if owners else {}

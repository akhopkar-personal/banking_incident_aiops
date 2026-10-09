"""Feedback candidates (Architecture Spec Section 10.3; Req. FR-27 to FR-29).

An Edit or Reject at review becomes a pending candidate in
data/feedback/candidates.json. The SME promotes or discards candidates on the
Review Queue page (promotion into the golden dataset is built in Phase 6).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from src import config
from src.logger_setup import log_interaction
from src.safety.redaction import redact_record
from src.schemas.feedback import FeedbackCandidate, ReviewDecision

_lock = threading.RLock()


def _file() -> Path:
    return config.get_settings().feedback_dir / "candidates.json"


def candidates() -> list[FeedbackCandidate]:
    path = _file()
    if not path.exists():
        return []
    with _lock:
        return [FeedbackCandidate.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def _save(items: list[FeedbackCandidate]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([c.model_dump(mode="json") for c in items], indent=1), encoding="utf-8")


def review_queue() -> list[FeedbackCandidate]:
    return [c for c in candidates() if c.status == "pending"]


def capture_candidate(decision: ReviewDecision, feedback_id: str, original_incident: dict[str, Any],
                      original_output: dict[str, Any], langfuse_trace_id: Optional[str] = None
                      ) -> FeedbackCandidate:
    """Record an Edit or Reject as a pending candidate (FR-27). Every text field is PII-checked."""
    if decision.decision not in ("edit", "reject"):
        raise ValueError("only edit and reject decisions become feedback candidates")
    correction = (json.dumps(decision.edited_output, ensure_ascii=False, sort_keys=True)
                  if decision.decision == "edit" else f"Rejected: {decision.reason}")
    (incident, output, correction, reason), _ = redact_record(
        [original_incident, original_output, correction, decision.reason or ""])
    with _lock:
        items = candidates()
        candidate = FeedbackCandidate(
            candidate_id=f"FC-{len(items) + 1:04d}", feedback_id=feedback_id, incident_id=decision.incident_id,
            run_id=decision.run_id, langfuse_trace_id=langfuse_trace_id, original_incident=incident,
            original_output=output, reviewer_correction=correction, reviewer_reason=reason,
            issue_type=decision.issue_type,
            prompt_version_set=(original_output.get("metadata") or {}).get("prompt_version_set") or {},
            created_at=decision.at)
        _save(items + [candidate])
    log_interaction("feedback_candidate_created", component="feedback_loop", incident_id=decision.incident_id,
                    run_id=decision.run_id, feedback_id=feedback_id, candidate_id=candidate.candidate_id,
                    issue_type=decision.issue_type.value if decision.issue_type else None)
    return candidate

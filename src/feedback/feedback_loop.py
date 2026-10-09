"""Feedback candidates (Architecture Spec Section 10.3; Req. FR-27 to FR-29).

An Edit or Reject at review becomes a pending candidate in
data/feedback/candidates.json. On the Review Queue page the SME promotes a
candidate into the golden dataset, with the ground truth, or discards it with a
reason (FR-28). A promoted case is mirrored to the LangFuse dataset.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src import config
from src.logger_setup import log_interaction
from src.safety.redaction import redact_record
from src.schemas.enums import IssueClass, ReviewerRole, SeverityLevel
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


def get(candidate_id: str) -> FeedbackCandidate:
    found = [c for c in candidates() if c.candidate_id == candidate_id]
    if not found:
        raise ValueError(f"unknown feedback candidate {candidate_id}")
    return found[0]


def _replace(updated: FeedbackCandidate) -> None:
    with _lock:
        _save([updated if c.candidate_id == updated.candidate_id else c for c in candidates()])


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


# ---------------------------------------------------------------- SME decisions

GROUND_TRUTH_FIELDS = ("expected_root_cause", "expected_severity", "expected_issue_class", "expected_runbook_id",
                       "expected_doc_ids", "must_cite_evidence_ids", "expected_causal_change_id",
                       "expected_regulatory_flag", "expected_abstention")


def default_ground_truth(candidate: FeedbackCandidate) -> dict[str, Any]:
    """A starting point for the SME: the scenario's seed ground truth, with the reviewer's edits applied."""
    from src.evaluation import golden_dataset

    scenario_id = candidate.original_incident.get("scenario_id")
    seed = next((c for c in golden_dataset.cases() if c["scenario_id"] == scenario_id
                 and c["variant"] == "replay"), {})
    truth = {k: seed.get(k) for k in GROUND_TRUTH_FIELDS}
    try:
        edits = json.loads(candidate.reviewer_correction)
    except ValueError:
        edits = {}
    if isinstance(edits, dict):
        truth["expected_root_cause"] = edits.get("root_cause") or truth["expected_root_cause"]
        truth["expected_severity"] = edits.get("severity") or truth["expected_severity"]
    truth["expected_abstention"] = bool(candidate.original_incident.get("withheld_sources")) or bool(
        truth.get("expected_abstention"))
    return truth


def _check_truth(truth: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in ("expected_root_cause", "expected_severity", "expected_issue_class", "expected_runbook_id")
               if not truth.get(k)]
    if missing:
        raise ValueError(f"the ground truth needs {missing}")
    level = SeverityLevel(truth["expected_severity"])
    IssueClass(truth["expected_issue_class"])
    doc_ids = list(dict.fromkeys([truth["expected_runbook_id"], *(truth.get("expected_doc_ids") or [])]))
    major = level.rank <= 2
    return {
        "expected_root_cause": str(truth["expected_root_cause"]).strip(), "expected_severity": level.value,
        "expected_issue_class": truth["expected_issue_class"], "expected_runbook_id": truth["expected_runbook_id"],
        "expected_doc_ids": doc_ids, "must_cite_evidence_ids": list(truth.get("must_cite_evidence_ids") or []),
        "expected_causal_change_id": truth.get("expected_causal_change_id") or None,
        "expected_dispatch": {"route": "page" if major else "assign", "fast_path": level.rank == 1,
                              "queue": "incident-escalation" if major else "production-support"},
        "expected_regulatory_flag": bool(truth.get("expected_regulatory_flag")),
        "expected_abstention": bool(truth.get("expected_abstention")),
    }


def promote(candidate_id: str, ground_truth: dict[str, Any], reviewer_role: ReviewerRole, reviewer_id: str,
            at: Optional[datetime] = None) -> str:
    """FR-28: add the candidate's incident to the golden dataset with SME ground truth. Returns the new version."""
    from src.evaluation import golden_dataset, langfuse_tracker

    if reviewer_role != ReviewerRole.SME:
        raise ValueError("only the SME promotes feedback candidates (FR-28)")
    candidate = get(candidate_id)
    if candidate.status != "pending":
        raise ValueError(f"{candidate_id} is already {candidate.status}")
    incident = candidate.original_incident
    scenario_id = incident.get("scenario_id")
    if not scenario_id:
        raise ValueError(f"{candidate_id} has no scenario to replay")
    truth, _ = redact_record(_check_truth(ground_truth))
    case_id = golden_dataset.next_case_id(scenario_id)
    input_id = case_id.replace("GC-", "TI-")
    mode = incident.get("input_mode", "detection_replay")
    test_input = {"input_id": input_id, "variant": "feedback", "scenario_id": scenario_id,
                  "reference_time": incident.get("reference_time"),
                  "withheld_sources": list(incident.get("withheld_sources") or []), "input_mode": mode,
                  "raw_input": "" if mode == "detection_replay" else incident.get("raw_input", ""),
                  "window": {"start": incident["window_start"], "end": incident["window_end"]}}
    new_case = {"case_id": case_id, "input_id": input_id, "scenario_id": scenario_id, "variant": "feedback",
                **truth, "source_candidate_id": candidate_id, "source_feedback_id": candidate.feedback_id}
    version = golden_dataset.add_case(new_case, test_input)
    at = at or datetime.now(timezone.utc)
    _replace(candidate.model_copy(update={"status": "promoted", "resolved_at": at, "resolved_by": reviewer_role,
                                          "golden_dataset_version_added": int(version.rsplit("v", 1)[1])}))
    log_interaction("feedback_candidate_promoted", component="feedback_loop", incident_id=candidate.incident_id,
                    run_id=candidate.run_id, feedback_id=candidate.feedback_id, candidate_id=candidate_id,
                    case_id=case_id, golden_dataset_version=version, reviewer_role=reviewer_role.value,
                    reviewer_id=reviewer_id)
    langfuse_tracker.attach_scores(candidate.langfuse_trace_id, {"feedback_candidate_status": "promoted"})
    langfuse_tracker.sync_golden_dataset(golden_dataset.cases([case_id]), version)
    return version


def discard(candidate_id: str, reason: str, reviewer_role: ReviewerRole, reviewer_id: str,
            at: Optional[datetime] = None) -> None:
    from src.evaluation import langfuse_tracker

    if reviewer_role != ReviewerRole.SME:
        raise ValueError("only the SME discards feedback candidates (FR-28)")
    if not reason.strip():
        raise ValueError("a reason is required to discard a candidate")
    candidate = get(candidate_id)
    if candidate.status != "pending":
        raise ValueError(f"{candidate_id} is already {candidate.status}")
    (reason,), _ = redact_record([reason.strip()])
    _replace(candidate.model_copy(update={"status": "discarded", "discard_reason": reason,
                                          "resolved_at": at or datetime.now(timezone.utc),
                                          "resolved_by": reviewer_role}))
    log_interaction("feedback_candidate_discarded", component="feedback_loop", incident_id=candidate.incident_id,
                    run_id=candidate.run_id, feedback_id=candidate.feedback_id, candidate_id=candidate_id,
                    reason=reason, reviewer_role=reviewer_role.value, reviewer_id=reviewer_id)
    langfuse_tracker.attach_scores(candidate.langfuse_trace_id, {"feedback_candidate_status": "discarded"})

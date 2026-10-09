"""Review, re-classification, page acknowledgement and gate hand-off
(Architecture Spec Section 10.1; Req. FR-19, FR-53, FR-58, Section 13.5 H-1 to H-5).

Every function validates the decision against the incident's recorded state,
appends an Incident State Store record and one or more preference records, and
logs the decision. The run's saved output is never modified: its review status
lives in the state store, and the approved output is identified by its content hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from src.logger_setup import log_interaction
from src.schemas.enums import InvestigationStatus, ReviewerRole, RiskLevel, SeverityLevel, SignalType
from src.schemas.feedback import GateHandoffPayload, Reclassification, ReviewDecision
from src.schemas.output import DispatchRecord
from src.services import incident_state_store as store

from . import feedback_loop, preference_store

REVIEW_STATUS = {"approve": InvestigationStatus.APPROVED, "edit": InvestigationStatus.EDITED,
                 "reject": InvestigationStatus.REJECTED}
# Fields a reviewer may change with Edit (H-3). Severity may only be lowered by a human here.
EDITABLE_FIELDS = ("root_cause", "severity", "recommended_actions", "stakeholder_summary")
RECLASSIFY_ROLES = (ReviewerRole.PRODUCTION_SUPPORT,)
ACKNOWLEDGE_ROLES = (ReviewerRole.ESCALATION, ReviewerRole.ONCALL, ReviewerRole.INCIDENT_COMMANDER)


def content_hash(output: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(output, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def run_context(incident_id: str, run_id: Optional[str] = None) -> dict[str, Any]:
    """The incident view, the run's saved output and Incident Object, and the IDs that every
    preference record carries. The latest run is used when `run_id` is not given."""
    view = store.current(incident_id)
    if view is None or not view.run_ids:
        raise ValueError(f"unknown incident {incident_id}")
    run_id = run_id or view.run_ids[-1]
    if run_id not in view.run_ids:
        raise ValueError(f"run {run_id} does not belong to {incident_id}")
    output = store.load_output(run_id)
    metadata = (output or {}).get("metadata") or {}
    return {"incident_id": incident_id, "run_id": run_id, "view": view, "output": output,
            "incident": store.load_incident(run_id), "scenario_id": view.scenario_id,
            "langfuse_trace_id": metadata.get("langfuse_trace_id"),
            "prompt_version_set": metadata.get("prompt_version_set") or {}}


def high_risk_steps(output: dict[str, Any]) -> list[int]:
    return [a["step"] for a in output.get("recommended_actions", []) if a.get("risk_level") == RiskLevel.HIGH.value]


def review_status(incident_id: str, run_id: str) -> str:
    """The status to show for a run: the human decision if there is one, else the output's own."""
    view = store.current(incident_id)
    review = (view.reviews.get(run_id) if view else None) or {}
    output = store.load_output(run_id) or {}
    return review.get("status") or output.get("status", "")


def _check_edit(decision: ReviewDecision, output: dict[str, Any]) -> None:
    edited = decision.edited_output or {}
    unknown = sorted(set(edited) - set(EDITABLE_FIELDS))
    if unknown:
        raise ValueError(f"these fields cannot be edited: {unknown}")
    if "severity" in edited:
        level = SeverityLevel(edited["severity"])
        current = SeverityLevel((output.get("severity") or {}).get("level", "S4"))
        if level.is_more_severe_than(current):
            raise ValueError("Edit can only lower severity; use Re-classify to raise an incident to Major")


def record_review(decision: ReviewDecision, reviewer_id: str) -> dict[str, Any]:
    """H-3 (and H-5): Approve, Edit or Reject one run's recommendation."""
    ctx = run_context(decision.incident_id, decision.run_id)
    output = ctx["output"]
    if output is None:
        raise ValueError(f"run {decision.run_id} has no saved output")
    if output["status"] == InvestigationStatus.SYSTEM_ERROR.value:
        raise ValueError("a SYSTEM_ERROR run has no recommendation to review")
    if decision.run_id in ctx["view"].reviews:
        raise ValueError(f"run {decision.run_id} was already reviewed")
    if decision.decision == "edit":
        _check_edit(decision, output)
    if decision.decision in ("approve", "edit"):
        unconfirmed = sorted(set(high_risk_steps(output)) - set(decision.high_risk_confirmations))
        if unconfirmed:
            raise ValueError(f"confirm the high-risk action steps {unconfirmed} before approving (H-5)")

    status = REVIEW_STATUS[decision.decision]
    review_pref = preference_store.append(preference_store.make(
        SignalType.REVIEW, ctx, decision.reviewer_role, reviewer_id,
        {"decision": decision.decision, "reason": decision.reason,
         "issue_type": decision.issue_type.value if decision.issue_type else None,
         "high_risk_confirmations": decision.high_risk_confirmations,
         "output_status": output["status"]}, decision.at))
    rating_pref = None
    if decision.ratings is not None:
        rating_pref = preference_store.append(preference_store.make(
            SignalType.RATING, ctx, decision.reviewer_role, reviewer_id,
            {**decision.ratings.model_dump(), "mean": decision.ratings.mean}, decision.at))
    candidate = None
    if decision.decision in ("edit", "reject"):
        candidate = feedback_loop.capture_candidate(decision, review_pref.feedback_id, ctx["incident"] or {},
                                                    output, ctx["langfuse_trace_id"])
    payload = {"status": status.value, "decision": decision.decision, "reason": decision.reason,
               "issue_type": decision.issue_type.value if decision.issue_type else None,
               "ratings": decision.ratings.model_dump() if decision.ratings else None,
               "high_risk_confirmations": decision.high_risk_confirmations,
               "edited_output": decision.edited_output, "content_hash": content_hash(output),
               "feedback_id": review_pref.feedback_id,
               "reviewer_id": reviewer_id, **({"candidate_id": candidate.candidate_id} if candidate else {})}
    store.append(decision.incident_id, decision.run_id, "reviewed", ctx["view"].state,
                 decision.reviewer_role.value, payload, at=decision.at)
    log_interaction("review_decision", component="review_store", incident_id=decision.incident_id,
                    run_id=decision.run_id, feedback_id=review_pref.feedback_id, decision=decision.decision,
                    status=status.value, reviewer_role=decision.reviewer_role.value,
                    issue_type=payload["issue_type"], high_risk_confirmations=decision.high_risk_confirmations,
                    reason=decision.reason, prompt_version_set=ctx["prompt_version_set"])
    if rating_pref is not None:
        log_interaction("rating_recorded", component="review_store", incident_id=decision.incident_id,
                        run_id=decision.run_id, feedback_id=rating_pref.feedback_id,
                        review_feedback_id=review_pref.feedback_id, ratings=decision.ratings.model_dump(),
                        mean=decision.ratings.mean, prompt_version_set=ctx["prompt_version_set"])
    from src.evaluation import langfuse_tracker

    langfuse_tracker.attach_human_scores(ctx["langfuse_trace_id"],
                                         decision.ratings.model_dump() if decision.ratings else None,
                                         status.value)
    return {"status": status, "feedback_id": review_pref.feedback_id,
            "candidate_id": candidate.candidate_id if candidate else None}


def record_reclassification(reclassification: Reclassification, reviewer_id: str) -> DispatchRecord:
    """H-4 / FR-53: Production Support raises the incident to Major; the final gate pages."""
    if reclassification.reviewer_role not in RECLASSIFY_ROLES:
        raise ValueError("only Production Support can re-classify an incident (FR-53)")
    if not reclassification.to_level.is_more_severe_than(reclassification.from_level):
        raise ValueError("re-classification must raise the severity")
    from src.agent.core_agent import run_reclassification

    ctx = run_context(reclassification.incident_id)
    payload = {"from_level": reclassification.from_level.value, "to_level": reclassification.to_level.value,
               "reason": reclassification.reason, "reviewer_id": reviewer_id}
    store.append(reclassification.incident_id, ctx["run_id"], "reclassified", ctx["view"].state,
                 reclassification.reviewer_role.value, payload, at=reclassification.at)
    dispatch = run_reclassification(reclassification.incident_id, reclassification)
    pref = preference_store.append(preference_store.make(
        SignalType.RECLASSIFICATION, ctx, reclassification.reviewer_role, reviewer_id, payload, reclassification.at))
    log_interaction("reclassified", component="review_store", incident_id=reclassification.incident_id,
                    run_id=ctx["run_id"], feedback_id=pref.feedback_id, from_level=payload["from_level"],
                    to_level=payload["to_level"], reviewer_role=reclassification.reviewer_role.value)
    return dispatch


def record_acknowledgement(incident_id: str, reviewer_role: ReviewerRole, reviewer_id: str) -> dict[str, Any]:
    """H-1: the escalation team acknowledges the simulated page. Records the time to acknowledge."""
    if reviewer_role not in ACKNOWLEDGE_ROLES:
        raise ValueError("the page is acknowledged by the escalation team, on-call or incident commander")
    ctx = run_context(incident_id)
    view = ctx["view"]
    if not view.paged_at:
        raise ValueError(f"{incident_id} was not paged")
    if view.acknowledged_at is not None:
        raise ValueError(f"{incident_id} was already acknowledged")
    at = store.now()
    seconds = round((at - view.paged_at[0]).total_seconds(), 1)
    store.append(incident_id, ctx["run_id"], "acknowledged", view.state, reviewer_role.value,
                 {"seconds_to_acknowledge": seconds, "reviewer_id": reviewer_id}, at=at)
    log_interaction("page_acknowledged", component="review_store", incident_id=incident_id, run_id=ctx["run_id"],
                    seconds_to_acknowledge=seconds, reviewer_role=reviewer_role.value)
    return {"acknowledged_at": at, "seconds_to_acknowledge": seconds}


def record_gate_handoff(incident_id: str, run_id: str, found_in_ranked_list: bool, rank_found: Optional[int],
                        reviewer_role: ReviewerRole, reviewer_id: str, note: str = "") -> str:
    """H-2: after a "Needs human RCA" hand-off, was the real cause in the ranked hypotheses?"""
    ctx = run_context(incident_id, run_id)
    if not (ctx["output"] or {}).get("needs_human_rca"):
        raise ValueError(f"run {run_id} was not handed off for human RCA")
    if run_id in ctx["view"].gate_handoffs:
        raise ValueError(f"the hand-off for run {run_id} was already recorded")
    handoff = GateHandoffPayload(found_in_ranked_list=found_in_ranked_list,
                                 rank_found=rank_found if found_in_ranked_list else None)
    at = store.now()
    payload = {**handoff.model_dump(), "note": note}
    pref = preference_store.append(preference_store.make(
        SignalType.GATE_HANDOFF, ctx, reviewer_role, reviewer_id, payload, at))
    store.append(incident_id, run_id, "gate_handoff", ctx["view"].state, reviewer_role.value,
                 {**payload, "feedback_id": pref.feedback_id}, at=at)
    log_interaction("gate_handoff_recorded", component="review_store", incident_id=incident_id, run_id=run_id,
                    feedback_id=pref.feedback_id, found_in_ranked_list=found_in_ranked_list,
                    rank_found=handoff.rank_found)
    return pref.feedback_id

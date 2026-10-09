"""Resolution and root-cause verification (Architecture Spec Section 10.2;
Req. FR-54, FR-55, Section 13.5 H-6 and H-7).

A confirmed or corrected verification closes the incident as CLOSED_VERIFIED
and indexes the resolution into the knowledge base; a rejected one closes it
as CLOSED_UNVERIFIED and nothing is indexed.
"""

from __future__ import annotations

from typing import Any

from src.logger_setup import log_error, log_interaction
from src.schemas.enums import IncidentState, SignalType
from src.schemas.feedback import ResolutionRecord, VerificationRecord
from src.services import incident_state_store as store
from src.evaluation import langfuse_tracker
from src.tool_retrieval import resolution_indexer

from . import preference_store
from .review_store import run_context

CLOSED = (IncidentState.CLOSED_VERIFIED, IncidentState.CLOSED_UNVERIFIED)


def record_resolution(resolution: ResolutionRecord, reviewer_id: str) -> str:
    """H-6: what was actually wrong, what was done and whether it worked. State RESOLVED."""
    ctx = run_context(resolution.incident_id)
    view = ctx["view"]
    if view.state in CLOSED or view.resolution is not None:
        raise ValueError(f"{resolution.incident_id} already has a resolution")
    payload = {"actual_root_cause": resolution.actual_root_cause, "actions_taken": resolution.actions_taken,
               "fix_outcome": resolution.fix_outcome.value, "resolution_time_min": resolution.resolution_time_min,
               "resolver_role": resolution.resolver_role.value, "reviewer_id": reviewer_id}
    pref = preference_store.append(preference_store.make(
        SignalType.FIX_OUTCOME, ctx, resolution.resolver_role, reviewer_id,
        {"fix_outcome": payload["fix_outcome"], "resolution_time_min": resolution.resolution_time_min},
        resolution.at))
    store.append(resolution.incident_id, ctx["run_id"], "resolved", IncidentState.RESOLVED,
                 resolution.resolver_role.value, {**payload, "feedback_id": pref.feedback_id}, at=resolution.at)
    log_interaction("resolution_recorded", component="resolution", incident_id=resolution.incident_id,
                    run_id=ctx["run_id"], feedback_id=pref.feedback_id, fix_outcome=payload["fix_outcome"],
                    resolution_time_min=resolution.resolution_time_min)
    langfuse_tracker.attach_scores(ctx["langfuse_trace_id"], {"fix_outcome": payload["fix_outcome"]})
    return pref.feedback_id


def record_verification(verification: VerificationRecord, reviewer_id: str) -> dict[str, Any]:
    """H-7: confirm, correct or reject the recorded root cause. Returns the indexing outcome."""
    ctx = run_context(verification.incident_id)
    view = ctx["view"]
    if view.resolution is None:
        raise ValueError(f"{verification.incident_id} has no resolution to verify")
    if view.state in CLOSED:
        raise ValueError(f"{verification.incident_id} is already closed")
    verified = verification.verdict in ("confirmed", "corrected")
    state = IncidentState.CLOSED_VERIFIED if verified else IncidentState.CLOSED_UNVERIFIED
    payload = {"verdict": verification.verdict, "corrected_root_cause": verification.corrected_root_cause,
               "verifier_role": verification.verifier_role.value, "reviewer_id": reviewer_id}
    pref = preference_store.append(preference_store.make(
        SignalType.VERIFICATION, ctx, verification.verifier_role, reviewer_id,
        {"verdict": verification.verdict}, verification.at))
    store.append(verification.incident_id, ctx["run_id"], "verified", state, verification.verifier_role.value,
                 {**payload, "feedback_id": pref.feedback_id}, at=verification.at)
    log_interaction("root_cause_verified", component="resolution", incident_id=verification.incident_id,
                    run_id=ctx["run_id"], feedback_id=pref.feedback_id, verdict=verification.verdict)
    langfuse_tracker.attach_scores(ctx["langfuse_trace_id"], {"root_cause_verified": verification.verdict})
    result: dict[str, Any] = {"state": state, "feedback_id": pref.feedback_id, "indexed": False, "doc_id": None,
                              "index_error": None}
    if verified:
        try:
            path = resolution_indexer.index_verified(verification.incident_id)
            result.update(indexed=True, doc_id=path.stem)
        except Exception as exc:  # noqa: BLE001 - the verification stands; indexing can be retried
            log_error("system_error", component="resolution", exc=exc, incident_id=verification.incident_id,
                      run_id=ctx["run_id"], failed_step="index_verified")
            result["index_error"] = "The resolution was verified but could not be indexed; see error.log."
    return result


def retry_indexing(incident_id: str) -> str:
    """Index a verified resolution whose indexing failed earlier."""
    view = store.current(incident_id)
    if view is None or view.state != IncidentState.CLOSED_VERIFIED:
        raise ValueError(f"{incident_id} is not CLOSED_VERIFIED")
    if view.indexed_doc_id:
        return view.indexed_doc_id
    return resolution_indexer.index_verified(incident_id).stem

"""LangGraph state: the working memory of one investigation run
(Architecture Spec Section 3.14; Req. Section 10.3.1).

The state is a TypedDict whose values are the Pydantic models defined in this
package. Fields that nodes running in parallel can write (for example the RCA
and Change Correlation agents) use the reducers below, so concurrent updates
are merged instead of one overwriting the other. Every other field is replaced
by the latest write.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from .analysis import ChangeCorrelationResult, RcaResult, RecommendationResult, RetrievedDocument, RulesResult, TriageResult
from .enums import ErrorType, RunMode
from .evidence import EvidenceItem
from .incident import IncidentObject
from .output import DispatchRecord, InvestigationOutput, SeverityAssessment

# ------------------------------------------------------------------ reducers


def merge_by_key(left: Optional[dict], right: Optional[dict]) -> dict:
    """Merge two dicts; keys in `right` win."""
    return {**(left or {}), **(right or {})}


def sum_by_key(left: Optional[dict], right: Optional[dict]) -> dict:
    """Merge two dicts of numbers, adding values that share a key."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        merged[key] = merged.get(key, 0) + value
    return merged


def append_list(left: Optional[list], right: Optional[list]) -> list:
    return list(left or []) + list(right or [])


def append_unique_documents(
    left: Optional[list[RetrievedDocument]], right: Optional[list[RetrievedDocument]]
) -> list[RetrievedDocument]:
    """Append retrieved documents, keeping the first copy of each doc_id + section."""
    merged: list[RetrievedDocument] = []
    seen: set[tuple[str, Optional[str]]] = set()
    for doc in list(left or []) + list(right or []):
        key = (doc.doc_id, doc.section)
        if key not in seen:
            seen.add(key)
            merged.append(doc)
    return merged


def merge_dispatch(left: Optional[DispatchRecord], right: Optional[DispatchRecord]) -> DispatchRecord:
    """Combine dispatch updates from several nodes.

    Flags only turn on. The first page time and ticket ID are kept (a later
    update cannot erase the fast-path page). The latest assigned queue wins.
    Notifications are combined without duplicates. Intended dispatch is merged.
    """
    if left is None:
        return right if right is not None else DispatchRecord()
    if right is None:
        return left
    notifications = list(left.notifications)
    seen = {n.outbox_id for n in notifications}
    notifications += [n for n in right.notifications if n.outbox_id not in seen]
    return DispatchRecord(
        paged=left.paged or right.paged,
        fast_path=left.fast_path or right.fast_path,
        page_time=left.page_time or right.page_time,
        itsm_ticket_id=left.itsm_ticket_id or right.itsm_ticket_id,
        assigned_queue=right.assigned_queue or left.assigned_queue,
        notifications=notifications,
        suppressed=left.suppressed or right.suppressed,
        intended={**left.intended, **right.intended},
    )


# --------------------------------------------------------------------- state


class NodeStatus(TypedDict, total=False):
    attempts: int
    last_error: Optional[str]
    ok: bool


class GraphState(TypedDict, total=False):
    # Set at entry.
    run_mode: RunMode
    raw_user_input: str
    supplied_window: Optional[dict[str, Any]]
    scenario_id: Optional[str]
    prompt_version_override: Optional[dict[str, str]]
    langfuse_trace_id: Optional[str]

    # Intake and rules.
    incident: IncidentObject
    rules_pass1: Optional[RulesResult]
    rules_pass2: Optional[RulesResult]
    rules_rescore: Optional[RulesResult]

    # Agents.
    triage: Optional[TriageResult]
    rca: Optional[RcaResult]
    changes: Optional[ChangeCorrelationResult]
    recommendation: Optional[RecommendationResult]
    evidence: Annotated[dict[str, EvidenceItem], merge_by_key]
    retrieved_documents: Annotated[list[RetrievedDocument], append_unique_documents]

    # Gates and dispatch.
    needs_human_rca: bool
    final_severity: Optional[SeverityAssessment]
    dispatch: Annotated[DispatchRecord, merge_dispatch]

    # Bookkeeping.
    guardrail_flags: Annotated[list[str], append_list]
    node_status: Annotated[dict[str, NodeStatus], merge_by_key]
    failed_node: Optional[str]
    error_type: Optional[ErrorType]
    token_usage: Annotated[dict[str, int], sum_by_key]
    latency_ms: Annotated[dict[str, int], sum_by_key]
    prompt_version_set: Annotated[dict[str, str], merge_by_key]

    # Result.
    output: Optional[InvestigationOutput]


def initial_state(
    raw_user_input: str,
    *,
    run_mode: RunMode = RunMode.LIVE,
    supplied_window: Optional[dict[str, Any]] = None,
    scenario_id: Optional[str] = None,
    prompt_version_override: Optional[dict[str, str]] = None,
) -> GraphState:
    """A fresh state for a new run. Nothing carries over between runs (Req. 10.3.1)."""
    if prompt_version_override and run_mode != RunMode.EVALUATION:
        raise ValueError("prompt_version_override is only allowed in evaluation run mode (Req. 10.4)")
    return GraphState(
        run_mode=run_mode,
        raw_user_input=raw_user_input,
        supplied_window=supplied_window,
        scenario_id=scenario_id,
        prompt_version_override=prompt_version_override,
        evidence={},
        retrieved_documents=[],
        dispatch=DispatchRecord(),
        guardrail_flags=[],
        node_status={},
        failed_node=None,
        error_type=None,
        token_usage={},
        latency_ms={},
        prompt_version_set={},
        needs_human_rca=False,
    )

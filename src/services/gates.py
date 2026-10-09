"""Gates and dispatch decisions (Architecture Spec Section 5.4; Req. FR-41 to FR-44,
FR-49, FR-51, FR-69).

Every function takes the graph state (a dict) and returns a partial update,
as a LangGraph node body does. No LLM is involved in any decision here.
Dispatch goes through the tool registry under the calling node's name, so the
access policy applies. In `evaluation` run mode nothing is dispatched: the
intended action is recorded in `dispatch.intended` instead.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src import config
from src.logger_setup import log_interaction
from src.schemas.analysis import RecommendationResult, RulesResult
from src.schemas.enums import IncidentState, RunMode, SeverityGroup, SeverityLevel
from src.schemas.output import DispatchRecord, SeverityAssessment
from src.services import incident_state_store as store
from src.services import severity_rules
from src.tools.implementations import ToolContext
from src.tools.tool_registry import get_registry

ESCALATION_TEAM = "incident-escalation"
SUPPORT_QUEUE = "production-support"


def tool_context(state: dict[str, Any]) -> ToolContext:
    incident = state["incident"]
    return ToolContext(scenario_id=incident.scenario_id, incident_id=incident.incident_id, run_id=incident.run_id,
                       withheld_sources=tuple(incident.withheld_sources), window_start=incident.window_start,
                       window_end=incident.window_end)


def _evaluation(state: dict[str, Any]) -> bool:
    return state.get("run_mode", RunMode.LIVE) == RunMode.EVALUATION


def _suppressed(state: dict[str, Any], action: str, details: dict[str, Any], node: str) -> dict[str, Any]:
    log_interaction("dispatch_suppressed", component=node, action=action, reason="evaluation_mode",
                    incident_id=state["incident"].incident_id, run_id=state["incident"].run_id)
    return {"dispatch": DispatchRecord(suppressed=True, intended={action: details})}


def latest_rules(state: dict[str, Any]) -> Optional[RulesResult]:
    return state.get("rules_rescore") or state.get("rules_pass2") or state.get("rules_pass1")


def _rules_text(rules: Optional[RulesResult]) -> str:
    if rules is None:
        return "no rules evaluated"
    fired = ", ".join(f"{r.rule_id} ({r.observed:g} vs {r.threshold:g})" for r in rules.rules_fired)
    return f"{rules.level.value} by rules: {fired or 'no rule fired'}"


def _state_now(incident_id: str) -> IncidentState:
    view = store.current(incident_id)
    return view.state if view else IncidentState.OPEN


# -------------------------------------------------------------- ITSM ticket

def upsert_ticket(state: dict[str, Any], node: str = "itsm_upsert") -> dict[str, Any]:
    """Create or update the incident's ticket (idempotent on the idempotency key, FR-44)."""
    dispatch: DispatchRecord = state.get("dispatch") or DispatchRecord()
    incident = state["incident"]
    rules = latest_rules(state)
    triage = state.get("triage")
    fields = {"incident_id": incident.incident_id, "severity": rules.level.value if rules else None,
              "issue_class": triage.issue_class.value if triage else None,
              "summary": f"{incident.incident_id}: {_rules_text(rules)}", "scenario_id": incident.scenario_id}
    if _evaluation(state):
        return _suppressed(state, "itsm_upsert", fields, node)
    line = get_registry().call("itsm_upsert_incident", {"idempotency_key": incident.idempotency_key,
                                                         "fields": fields}, node, tool_context(state))
    if not dispatch.itsm_ticket_id:
        store.append(incident.incident_id, incident.run_id, "ticket_upserted", _state_now(incident.incident_id), node,
                     {"ticket_id": line["ticket_id"], "created": line["created"]})
    log_interaction("dispatch_itsm", component=node, operation="upsert", ticket_id=line["ticket_id"],
                    created=line["created"], incident_id=incident.incident_id, run_id=incident.run_id)
    return {"dispatch": DispatchRecord(itsm_ticket_id=line["ticket_id"])}


# -------------------------------------------------------------------- pages

def _page(state: dict[str, Any], node: str, summary: str, *, fast_path: bool) -> dict[str, Any]:
    incident = state["incident"]
    dispatch: DispatchRecord = state.get("dispatch") or DispatchRecord()
    view = store.current(incident.incident_id)
    # Paged before, in this run or an earlier one (a deduplicated repeat): attach to that page,
    # never page twice (FR-38, FR-51). This also keeps FR-69's one page per 15 minutes. In evaluation
    # mode an intended page counts as sent, so the intended record mirrors a live run.
    if dispatch.paged or "page" in dispatch.intended or (view is not None and view.paged_at):
        if _evaluation(state):
            return _suppressed(state, "page_update", {"team": ESCALATION_TEAM, "summary": summary}, node)
        line = get_registry().call("page_oncall", {"incident_id": incident.incident_id, "team": ESCALATION_TEAM,
                                                   "summary": summary, "mode": "update"}, node, tool_context(state))
        store.append(incident.incident_id, incident.run_id, "paged", IncidentState.PAGED, node,
                     {"mode": "update", "outbox_id": line["outbox_id"]})
        log_interaction("dispatch_page", component=node, mode="update", outbox_id=line["outbox_id"],
                        incident_id=incident.incident_id, run_id=incident.run_id)
        return {} if dispatch.paged else {"dispatch": DispatchRecord(paged=True)}
    if _evaluation(state):
        return _suppressed(state, "page", {"team": ESCALATION_TEAM, "summary": summary, "fast_path": fast_path}, node)
    line = get_registry().call("page_oncall", {"incident_id": incident.incident_id, "team": ESCALATION_TEAM,
                                               "summary": summary, "mode": "new"}, node, tool_context(state))
    page_time = datetime.fromisoformat(line["at"])
    store.append(incident.incident_id, incident.run_id, "paged", IncidentState.PAGED, node,
                 {"mode": "new", "fast_path": fast_path, "outbox_id": line["outbox_id"]}, at=page_time)
    log_interaction("fast_path_page" if fast_path else "dispatch_page", component=node, mode="new",
                    team=ESCALATION_TEAM, outbox_id=line["outbox_id"], page_time=line["at"],
                    incident_id=incident.incident_id, run_id=incident.run_id)
    return {"dispatch": DispatchRecord(paged=True, fast_path=fast_path, page_time=page_time)}


def fast_page(state: dict[str, Any]) -> dict[str, Any]:
    """Critical fast path (FR-42): page on an S1 from the first rules pass, before any LLM call."""
    rules: Optional[RulesResult] = state.get("rules_pass1")
    if rules is None or rules.level != SeverityLevel.S1:
        return {}
    summary = f"{state['incident'].incident_id} S1 (fast path, rules only): {_rules_text(rules)}"
    return _page(state, "dispatch_fast_page", summary, fast_path=True)


def page(state: dict[str, Any], node: str = "dispatch_page") -> dict[str, Any]:
    """Major incident (FR-51). If the fast path already paged, attach the recommendation instead."""
    final: Optional[SeverityAssessment] = state.get("final_severity")
    recommendation: Optional[RecommendationResult] = state.get("recommendation")
    summary = f"{state['incident'].incident_id} {final.level.value if final else ''}: " + (
        recommendation.root_cause_summary if recommendation else _rules_text(latest_rules(state)))
    return _page(state, node, summary, fast_path=False)


# --------------------------------------------------------------- assignment

def assign(state: dict[str, Any], node: str = "dispatch_itsm_assign") -> dict[str, Any]:
    """Low/Medium (FR-51): assign the ticket to the Production Support queue with summary and fix."""
    incident = state["incident"]
    dispatch: DispatchRecord = state.get("dispatch") or DispatchRecord()
    update: dict[str, Any] = {}
    ticket = dispatch.itsm_ticket_id
    if not ticket:
        update = upsert_ticket(state, node)
        ticket = update["dispatch"].itsm_ticket_id
    recommendation: Optional[RecommendationResult] = state.get("recommendation")
    summary = recommendation.root_cause_summary if recommendation else _rules_text(latest_rules(state))
    fix = "; ".join(a.action for a in recommendation.recommended_actions) if recommendation else \
        "No AI recommendation (rules-only). Investigate with the runbooks for the affected service."
    if _evaluation(state):
        suppressed = _suppressed(state, "itsm_assign", {"queue": SUPPORT_QUEUE, "summary": summary, "fix": fix}, node)
        return {"dispatch": _merge(update.get("dispatch"), suppressed["dispatch"])}
    get_registry().call("itsm_assign", {"ticket_id": ticket, "queue": SUPPORT_QUEUE, "summary": summary, "fix": fix,
                                        "incident_id": incident.incident_id}, node, tool_context(state))
    store.append(incident.incident_id, incident.run_id, "assigned", IncidentState.ASSIGNED, node,
                 {"queue": SUPPORT_QUEUE, "ticket_id": ticket})
    log_interaction("dispatch_itsm", component=node, operation="assign", ticket_id=ticket, queue=SUPPORT_QUEUE,
                    incident_id=incident.incident_id, run_id=incident.run_id)
    return {"dispatch": _merge(update.get("dispatch"), DispatchRecord(itsm_ticket_id=ticket,
                                                                      assigned_queue=SUPPORT_QUEUE))}


def _merge(a: Optional[DispatchRecord], b: Optional[DispatchRecord]) -> DispatchRecord:
    from src.schemas.graph_state import merge_dispatch

    return merge_dispatch(a, b)


# -------------------------------------------------------- severity and gate

def confidence_gate(recommendation: Optional[RecommendationResult]) -> dict[str, Any]:
    """FR-49: flag 'Needs human RCA' when the top confidence is below the threshold."""
    threshold = config.get_settings().confidence_gate_threshold
    needs = recommendation is None or recommendation.top_confidence < threshold
    return {"needs_human_rca": needs}


def final_severity(state: dict[str, Any]) -> dict[str, Any]:
    """The rules decide (FR-41). A higher AI proposal only adds a flag and is logged."""
    rules = latest_rules(state)
    if rules is None:
        raise ValueError("final severity needs a rules result")
    triage = state.get("triage")
    proposed = triage.proposed_severity if triage else None
    flags = list(rules.flags)
    if proposed is not None and proposed.is_more_severe_than(rules.level):
        flags.append("ai_suggests_higher_severity")
        incident = state["incident"]
        log_interaction("severity_disagreement", component="final_severity_gate", rules_level=rules.level.value,
                        llm_proposed_level=proposed.value, incident_id=incident.incident_id, run_id=incident.run_id,
                        scenario_id=incident.scenario_id, rule_ids=[r.rule_id for r in rules.rules_fired])
    assessment = SeverityAssessment(level=rules.level, rationale=_rules_text(rules), llm_proposed_level=proposed,
                                    rules_fired=rules.rules_fired, rescored=state.get("rules_rescore") is not None,
                                    flags=flags)
    return {"final_severity": assessment}


# ---------------------------------------------------------------- rules-only

def rules_only(state: dict[str, Any], node: str = "rules_only_dispatch") -> dict[str, Any]:
    """FR-43 fallback: rules severity, ticket, and page (Major) or assignment (Low/Medium),
    skipping anything already done, so it is safe after any failure."""
    incident = state["incident"]
    update: dict[str, Any] = {}
    working = dict(state)
    if working.get("rules_pass1") is None:
        signals = severity_rules.compute_signals(incident.scenario_id, incident.window_start, incident.window_end,
                                                 incident.withheld_sources)
        update["rules_pass1"] = working["rules_pass1"] = severity_rules.evaluate("pass1", signals)
        severity_rules.log_result(update["rules_pass1"], node)
    rules = latest_rules(working)
    dispatch = working.get("dispatch") or DispatchRecord()
    collected: Optional[DispatchRecord] = None

    def apply(step: dict[str, Any]) -> None:
        nonlocal dispatch, collected
        if step.get("dispatch") is not None:
            dispatch = _merge(dispatch, step["dispatch"])
            collected = _merge(collected, step["dispatch"])
            working["dispatch"] = dispatch

    if not dispatch.itsm_ticket_id and not (dispatch.suppressed and "itsm_upsert" in dispatch.intended):
        apply(upsert_ticket(working, node))
    if rules.group == SeverityGroup.MAJOR:
        if not dispatch.paged and "page" not in dispatch.intended:
            summary = f"{incident.incident_id} {rules.level.value} (rules only, no AI investigation): {_rules_text(rules)}"
            apply(_page(working, node, summary, fast_path=False))
    elif not dispatch.assigned_queue and "itsm_assign" not in dispatch.intended:
        apply(assign(working, node))
    if collected is not None:
        update["dispatch"] = collected
    return update

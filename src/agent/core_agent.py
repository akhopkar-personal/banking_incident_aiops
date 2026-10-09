"""Incident Workflow Engine: the LangGraph StateGraph (Architecture Spec Section 4).

No LLM routing: every edge is decided by code. Nodes are wrapped (Section 4.4)
so a failure never raises out of the graph: it marks the node failed, and the
guarded edge sends the run to `rules_only_dispatch` and `system_error`.

Entry points: `run_investigation`, `run_reclassification`, `build_graph`.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Callable, Optional, Union

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from src import config
from src.agent import change_correlation_agent, rca_agent, recommendation_agent, triage_agent
from src.agent import llm as llm_module
from src.agent.change_correlation_agent import onset_of
from src.errors import CostCapExceeded, RecoverableError
from src.logger_setup import log_context, log_error, log_interaction
from src.safety import guardrails, output_checks
from src.safety.injection_detector import scan
from src.safety.redaction import redact
from src.schemas.analysis import SummaryFields
from src.schemas.enums import ErrorType, IncidentState, InputMode, InvestigationStatus, RunMode, SeverityGroup, SeverityLevel
from src.schemas.evidence import EvidenceItem
from src.schemas.feedback import Reclassification
from src.schemas.graph_state import GraphState, initial_state, merge_dispatch
from src.schemas.incident import IncidentObject
from src.schemas.output import (
    DispatchRecord, ErrorDetail, InvestigationOutput, OutputMetadata, SeverityAssessment,
)
from src.services import alert_correlator, anomaly_detector, gates, notification_service, severity_rules
from src.services import incident_state_store as store
from src.tools.implementations._common import parse_time

AGENT_NODES = ("triage_agent", "rca_agent", "change_correlation_agent", "recommendation_agent")


class IntakeRejection(BaseModel):
    """The input was not accepted; no incident was created (Section 4.1)."""

    reason: str  # not_an_incident_report, alert_json_missing_required_fields, no_time_window, no_data_source,
    #              internal_error
    message: str


# ================================================================== intake

INCIDENT_WORDS = re.compile(
    r"\b(fail\w*|error\w*|down|outage|slow\w*|latenc\w*|time[sd]? ?out|timeout\w*|complain\w*|charged|twice|"
    r"double|declin\w*|not (seeing|receiving|getting|working|loading)|missing|stuck|lag\w*|alert\w*|incident|"
    r"degrad\w*|unavailable|broken|expired|cannot|can't)\b", re.I)
SERVICE_HINTS = {
    "payments-service": ("payment", "transfer", "card", "charged", "debit"), "auth-service": ("login", "log in", "otp"),
    "accounts-service": ("balance", "statement", "account"), "notification-service": ("sms", "alert", "notification"),
    "payment-network-gateway": ("switch", "upi", "neft"), "kafka-platform": ("kafka", "eventhub", "broker", "consumer"),
    "core-banking-db": ("database", " db ", "core banking"), "mobile-app": ("app",), "net-banking": ("net banking", "web"),
}
SEVERITY_WORDS = {"critical": "S1", "high": "S2", "major": "S2", "medium": "S3", "low": "S4", "minor": "S4"}
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:?\d{2})?")
_CLOCK = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(UTC|GMT|Z)?\b", re.I)


def _manifest(scenario_id: str) -> dict[str, Any]:
    return json.loads((config.get_settings().scenario_dir(scenario_id) / "manifest.json").read_text(encoding="utf-8"))


def _fixture_flags(scenario_id: str) -> dict[str, Any]:
    path = config.get_settings().scenario_dir(scenario_id) / "fixture.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _parse_moment(text: str, default_date: datetime) -> tuple[Optional[datetime], bool]:
    """(time, assumed_utc) from ISO text or a clock time like '10:05 UTC' on default_date."""
    iso = _ISO.search(text)
    if iso:
        zone = iso.group(3)
        moment = datetime.fromisoformat(f"{iso.group(1)}T{iso.group(2)}{'' if not zone or zone == 'Z' else zone}")
        assumed = zone is None
        return (moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)), assumed
    clock = _CLOCK.search(text)
    if clock:
        moment = default_date.replace(hour=int(clock.group(1)), minute=int(clock.group(2)), second=0, microsecond=0)
        return moment, clock.group(3) is None
    return None, False


def _reject(reason: str, message: str) -> dict[str, Any]:
    return {"intake_rejection": {"reason": reason, "message": message}}


def classify_request(state: dict[str, Any]) -> dict[str, Any]:
    """Normalize the input (Req. 6.2, 6.3). Returns {'intake': ...} or {'intake_rejection': ...}."""
    scenario_id = state.get("scenario_id")
    if not scenario_id:
        return _reject("no_data_source", "Choose the scenario whose telemetry this investigation should read.")
    manifest = _manifest(scenario_id)
    scenario_date = parse_time(manifest["reference_time"])
    raw = (state.get("raw_user_input") or "").strip()
    window = state.get("supplied_window") or None
    given_reference = state.get("reference_time")
    out: dict[str, Any] = {"scenario_id": scenario_id, "reported_service": None, "reported_severity": None,
                           "hints": [], "timezone_assumed_utc": False}

    if not raw:
        out["input_mode"] = InputMode.DETECTION_REPLAY
        out["reference_time"] = given_reference or scenario_date
        window = window or {"start": manifest["window_start"], "end": manifest["window_end"]}
    elif raw.startswith("{"):
        out["input_mode"] = InputMode.ALERT_JSON
        try:
            alert = json.loads(raw)
        except ValueError:
            return _reject("alert_json_missing_required_fields", "The alert is not valid JSON.")
        missing = [k for k in ("service", "timestamp") if not alert.get(k)]
        if missing:
            return _reject("alert_json_missing_required_fields", f"The alert JSON needs {', '.join(missing)}.")
        moment, assumed = _parse_moment(str(alert["timestamp"]), scenario_date)
        if moment is None:
            return _reject("alert_json_missing_required_fields", "The alert timestamp is not an ISO 8601 time.")
        out.update(reference_time=given_reference or moment, timezone_assumed_utc=assumed,
                   reported_service=str(alert["service"]))
        severity = str(alert.get("severity") or alert.get("reported_severity") or "").strip()
        out["reported_severity"] = severity.upper() if severity.upper() in ("S1", "S2", "S3", "S4") else \
            SEVERITY_WORDS.get(severity.lower())
        out["hints"] = [str(alert.get(k)) for k in ("alert_name", "summary", "condition") if alert.get(k)]
    else:
        out["input_mode"] = InputMode.FREE_TEXT
        if not INCIDENT_WORDS.search(raw):
            return _reject("not_an_incident_report",
                           "This does not describe an incident. Describe what is failing and since when.")
        moment, assumed = _parse_moment(raw, scenario_date)
        if moment is None and given_reference is None and not window:
            return _reject("no_time_window", "Add a time (for example 'since 10:05 UTC') or a time window.")
        lower = f" {raw.lower()} "
        out["reported_service"] = next((s for s, words in SERVICE_HINTS.items() if any(w in lower for w in words)), None)
        out.update(reference_time=given_reference or moment or parse_time(window["start"]),
                   timezone_assumed_utc=assumed and moment is not None and given_reference is None,
                   hints=sorted({m.group(0).lower() for m in INCIDENT_WORDS.finditer(raw)}))
    reference = out["reference_time"]
    if window:
        out["window_start"], out["window_end"] = parse_time(window["start"]), parse_time(window["end"])
        if out["window_end"] <= out["window_start"]:
            return _reject("no_time_window", "The window end must be after its start.")
    else:
        out["window_start"], out["window_end"] = reference - timedelta(minutes=60), reference + timedelta(minutes=15)
    out["raw_input"] = raw
    return {"intake": out, "llm_available": bool(_fixture_flags(scenario_id).get("llm_enabled", True))}


# ================================================================== nodes

def node_intake(state, config=None):
    return classify_request(state)


def node_correlate_dedup(state, config=None):
    intake, run_id = state["intake"], state["run_id"]
    withheld = state.get("withheld_sources") or []
    events = anomaly_detector.detect(intake["scenario_id"], intake["window_start"], intake["window_end"], withheld)
    group = alert_correlator.primary_group(alert_correlator.group(events))
    if group is None:  # manual input with nothing detected: still one incident per service and bucket
        root = intake.get("reported_service") or "unknown"
        bucket = intake["reference_time"].replace(minute=intake["reference_time"].minute // 30 * 30, second=0,
                                                  microsecond=0)
        group = alert_correlator.IncidentGroup(root_service=root, events=[], bucket_start=bucket,
                                               idempotency_key=alert_correlator.idempotency_key(root, bucket))
    incident_id, created = alert_correlator.upsert_incident(group, reference_time=intake["reference_time"],
                                                            run_id=run_id, scenario_id=intake["scenario_id"])
    text, _ = redact(intake["raw_input"])  # redaction first: nothing unredacted enters the incident
    incident = IncidentObject(
        incident_id=incident_id, run_id=run_id, idempotency_key=group.idempotency_key,
        input_mode=intake["input_mode"], scenario_id=intake["scenario_id"], raw_input=text,
        anomaly_events=group.events, reported_service=intake.get("reported_service") or group.root_service,
        reported_severity=intake.get("reported_severity"), reference_time=intake["reference_time"],
        window_start=intake["window_start"], window_end=intake["window_end"], hints=intake.get("hints", []),
        timezone_assumed_utc=intake.get("timezone_assumed_utc", False), withheld_sources=list(withheld))
    return {"incident": incident, "deduplicated": not created}


def node_redact(state, config=None):
    incident: IncidentObject = state["incident"]
    _, pii = redact(state["intake"]["raw_input"])
    hints = [redact(h)[0] for h in incident.hints]
    flags = [f"input_{f}" for f in pii] + scan(state["intake"]["raw_input"]) + [
        f for h in incident.hints for f in scan(h)]
    if incident.timezone_assumed_utc:
        flags.append("timezone_assumed_utc")
    return {"incident": incident.model_copy(update={"hints": hints}), "guardrail_flags": list(dict.fromkeys(flags))}


def _signals(state):
    incident = state["incident"]
    return severity_rules.compute_signals(incident.scenario_id, incident.window_start, incident.window_end,
                                          incident.withheld_sources)


def node_rules_pass1(state, config=None):
    result = severity_rules.evaluate("pass1", _signals(state))
    severity_rules.log_result(result, "severity_rules_pass1")
    return {"rules_pass1": result}


def node_rules_pass2(state, config=None):
    result = severity_rules.evaluate("pass2", _signals(state), state.get("triage"), state.get("rules_pass1"))
    severity_rules.log_result(result, "severity_rules_pass2")
    return {"rules_pass2": result}


def node_rescore(state, config=None):
    signals = severity_rules.apply_confirmed_inputs(_signals(state), state["rca"].severity_inputs_found)
    result = severity_rules.evaluate("rescore", signals, state.get("triage"), state.get("rules_pass2"))
    severity_rules.log_result(result, "severity_rescore")
    return {"rules_rescore": result}


def node_fast_page(state, config=None):
    return gates.fast_page(state)


def node_itsm_upsert(state, config=None):
    return gates.upsert_ticket(state)


def node_analysis_join(state, config=None):
    return {}


def node_output_guardrails(state, config=None):
    result = guardrails.run_output_guardrails(state)
    incident = state["incident"]
    for flag in result.flags:
        if flag in ("action_denied", "unknown_action_type", "uncited_high_risk", "reordered", "risk_raised"):
            log_interaction("action_policy_flag", component="output_guardrails", flag=flag,
                            incident_id=incident.incident_id, run_id=incident.run_id)
    if result.flags:
        log_interaction("guardrail_block", component="output_guardrails", flags=result.flags,
                        incident_id=incident.incident_id, run_id=incident.run_id)
    return {"recommendation": result.recommendation, "rca": result.rca,
            "recommended_actions": result.recommended_actions, "stakeholder_summary": result.stakeholder_summary,
            "insufficient_evidence": result.insufficient_evidence, "guardrail_flags": result.flags,
            "action_policy_version": result.action_policy_version}


def node_confidence_gate(state, config=None):
    update = gates.confidence_gate(state.get("recommendation"))
    if update["needs_human_rca"]:
        incident = state["incident"]
        top = state["recommendation"].top_confidence if state.get("recommendation") else None
        log_interaction("confidence_gate_flagged", component="confidence_gate", top_confidence=top,
                        incident_id=incident.incident_id, run_id=incident.run_id)
    return update


def node_final_severity(state, config=None):
    return gates.final_severity(state)


def node_dispatch_page(state, config=None):
    return gates.page(state)


def node_dispatch_assign(state, config=None):
    return gates.assign(state)


def _summary_fields(state) -> SummaryFields:
    recommendation = state.get("recommendation")
    if recommendation is not None:
        return recommendation.summary_fields
    incident = state["incident"]
    return SummaryFields(what_happened=f"Incident on {incident.reported_service or 'banking services'}.",
                         customer_impact="Customer impact is being assessed.",
                         current_status="Under investigation.", next_update="Within 60 minutes.")


def node_notify(state, config=None):
    incident, final = state["incident"], state["final_severity"]
    sent, intended = notification_service.notify(
        incident.incident_id, incident.run_id, final, _summary_fields(state), state.get("needs_human_rca", False),
        incident.reported_service or "unknown", state.get("run_mode", RunMode.LIVE), gates.tool_context(state))
    return {"dispatch": DispatchRecord(notifications=sent, intended=intended,
                                       suppressed=bool(intended))}


def node_rules_only(state, config=None):
    if state.get("incident") is None:
        return {}
    update = gates.rules_only(state)
    if not state.get("failed_node") and not config_llm_enabled():
        incident = state["incident"]
        log_interaction("kill_switch_active", component="rules_only_dispatch", incident_id=incident.incident_id,
                        run_id=incident.run_id)
        update.update(failed_node="kill_switch", error_type=ErrorType.KILL_SWITCH)
    return update


def config_llm_enabled() -> bool:
    return config.get_settings().llm_enabled


# ---------------------------------------------------------------- outputs

def _metadata(state, rules_version: Optional[str]) -> OutputMetadata:
    budget = llm_module.budget(state["run_id"])
    return OutputMetadata(
        model=config.get_settings().llm_model, token_counts=dict(state.get("token_usage") or {}),
        cost_estimate_usd=round(budget.cost_usd, 6), latency_ms_per_stage=dict(state.get("latency_ms") or {}),
        guardrail_flags=list(dict.fromkeys(state.get("guardrail_flags") or [])),
        timezone_assumed_utc=state["incident"].timezone_assumed_utc if state.get("incident") else False,
        langfuse_trace_id=state.get("langfuse_trace_id"), prompt_version_set=dict(state.get("prompt_version_set") or {}),
        rules_version=rules_version, action_policy_version=state.get("action_policy_version"),
        run_mode=state.get("run_mode", RunMode.LIVE))


def _state_now(incident_id: str) -> IncidentState:
    view = store.current(incident_id)
    return view.state if view else IncidentState.OPEN


def _referenced_evidence(state) -> dict[str, EvidenceItem]:
    ids: set[str] = set()
    if state.get("triage"):
        ids.update(state["triage"].evidence_ids)
    if state.get("rca"):
        ids.update(e.evidence_id for e in state["rca"].timeline)
        ids.update(e for h in state["rca"].hypotheses for e in h.supporting_evidence)
    if state.get("changes"):
        ids.update(f.change_id for f in state["changes"].findings)
    if state.get("recommendation"):
        ids.update(state["recommendation"].cited_evidence_ids)
    evidence = state.get("evidence") or {}
    return {i: evidence[i] for i in sorted(ids) if i in evidence}


def node_finalize(state, config=None):
    incident = state["incident"]
    triage, rca, rec = state.get("triage"), state.get("rca"), state.get("recommendation")
    insufficient = bool(state.get("insufficient_evidence")) or bool(rec and rec.insufficient_evidence_reason)
    status = InvestigationStatus.INSUFFICIENT_EVIDENCE if insufficient else InvestigationStatus.PENDING_REVIEW
    reason = None
    if insufficient:
        reason = (rec.insufficient_evidence_reason if rec and rec.insufficient_evidence_reason else
                  "The evidence does not support a grounded recommendation; investigate manually.")
    rules = gates.latest_rules(state)
    output = InvestigationOutput(
        incident_id=incident.incident_id, run_id=incident.run_id, status=status,
        incident_state=_state_now(incident.incident_id), issue_class=triage.issue_class,
        severity=state["final_severity"], affected_services=triage.affected_services,
        timeline=rca.timeline if rca else [], hypotheses=rca.hypotheses if rca else [],
        change_findings=state["changes"].findings if state.get("changes") else [],
        evidence=_referenced_evidence(state),
        recommended_actions=[] if insufficient else list(state.get("recommended_actions") or []),
        needs_human_rca=bool(state.get("needs_human_rca")),
        stakeholder_summary=state.get("stakeholder_summary") or guardrails.render_summary(rec) if rec else
        "The incident is under investigation.",
        regulatory_notes=(rec.regulatory_notes or None) if rec else None, insufficient_evidence_reason=reason,
        dispatch=state.get("dispatch"), metadata=_metadata(state, rules.rules_version if rules else None))
    output, _ = _recheck_output(output)
    store.save_output(output)
    _save_incident(incident)
    return {"output": output}


def _recheck_output(output: InvestigationOutput) -> tuple[InvestigationOutput, list[str]]:
    """PII re-check of the whole output before display, logging and storage (Req. 13.3)."""
    data, flags = output_checks.recheck_pii(output.model_dump(mode="json"))
    if flags:
        data["metadata"]["guardrail_flags"] = list(dict.fromkeys(data["metadata"]["guardrail_flags"] + flags))
    return InvestigationOutput.model_validate(data), flags


def _save_incident(incident: IncidentObject) -> None:
    path = config.get_settings().incident_state_dir / "outputs" / f"{incident.run_id}.incident.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(incident.model_dump_json(indent=1), encoding="utf-8")


def node_system_error(state, config=None):
    incident = state.get("incident")
    if incident is None:
        return {}
    failed = state.get("failed_node") or "unknown"
    error_type = state.get("error_type") or ErrorType.UNHANDLED_EXCEPTION
    attempts = ((state.get("node_status") or {}).get(failed) or {}).get("attempts", 1)
    detail, _ = output_checks.check_error_message(ErrorDetail(
        failed_node=failed, error_type=error_type, retry_count=max(0, attempts - 1),
        message=output_checks.STANDARD_ERROR_MESSAGES[error_type]))
    rules = gates.latest_rules(state)
    output = InvestigationOutput(
        incident_id=incident.incident_id, run_id=incident.run_id, status=InvestigationStatus.SYSTEM_ERROR,
        incident_state=_state_now(incident.incident_id), dispatch=state.get("dispatch"), rules_severity=rules,
        error_detail=detail, metadata=_metadata(state, rules.rules_version if rules else None))
    output, _ = _recheck_output(output)
    store.save_output(output)
    _save_incident(incident)
    log_error("system_error", component="system_error", failed_node=failed, error_type=error_type.value,
              retry_count=detail.retry_count, rules_severity=rules.level.value if rules else None,
              paged=bool(state.get("dispatch") and state["dispatch"].paged),
              incident_id=incident.incident_id, run_id=incident.run_id)
    return {"output": output}


# ============================================================ node wrapper

def wrap(name: str, body: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Section 4.4: run the body; on success record status and latency; on failure record the
    failure and return normally, so the guarded edge can route to the fallback."""

    def node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        incident = state.get("incident")
        started = time.perf_counter()
        with log_context(incident_id=incident.incident_id if incident else None, run_id=state.get("run_id"),
                         langfuse_trace_id=state.get("langfuse_trace_id")):
            try:
                update = body(state, config) or {}
            except CostCapExceeded as exc:
                error_type, attempts, error = ErrorType.COST_CAP_EXCEEDED, 1, exc
            except RecoverableError as exc:
                error_type, attempts, error = exc.error_type, getattr(exc, "attempts", 1), exc
            except Exception as exc:  # noqa: BLE001 - the graph must always leave a usable state
                error_type, attempts, error = ErrorType.UNHANDLED_EXCEPTION, 1, exc
            else:
                latency = round((time.perf_counter() - started) * 1000)
                if incident is not None or update.get("incident") is not None:
                    log_interaction("node_completed", component=name, latency_ms=latency,
                                    incident_id=(incident or update["incident"]).incident_id,
                                    run_id=state.get("run_id"))
                return {**update, "node_status": {name: {"ok": True, "attempts": 1}}, "latency_ms": {name: latency}}
            latency = round((time.perf_counter() - started) * 1000)
            log_error("system_error", component=name, exc=error, stage="node_failed", error_type=error_type.value,
                      attempts=attempts, incident_id=incident.incident_id if incident else "unknown",
                      run_id=state.get("run_id") or "unknown")
            return {"node_status": {name: {"ok": False, "attempts": attempts, "last_error": type(error).__name__}},
                    "failed_node": name, "error_type": error_type, "latency_ms": {name: latency}}

    node.__name__ = name
    return node


def _ok(state, name: str) -> bool:
    return bool(((state.get("node_status") or {}).get(name) or {}).get("ok"))


def guarded(source: str, target: str) -> Callable[[dict[str, Any]], str]:
    return lambda state: target if _ok(state, source) else "rules_only_dispatch"


# ================================================================== graph

NODES: dict[str, Callable[..., dict[str, Any]]] = {
    "intake": node_intake, "correlate_dedup": node_correlate_dedup, "redact": node_redact,
    "severity_rules_pass1": node_rules_pass1, "dispatch_fast_page": node_fast_page,
    "triage_agent": triage_agent.run, "itsm_upsert": node_itsm_upsert, "severity_rules_pass2": node_rules_pass2,
    "rca_agent": rca_agent.run, "change_correlation_agent": change_correlation_agent.run,
    "analysis_join": node_analysis_join, "recommendation_agent": recommendation_agent.run,
    "severity_rescore": node_rescore, "output_guardrails": node_output_guardrails,
    "confidence_gate": node_confidence_gate, "final_severity_gate": node_final_severity,
    "dispatch_page": node_dispatch_page, "dispatch_itsm_assign": node_dispatch_assign, "notify": node_notify,
    "finalize": node_finalize, "rules_only_dispatch": node_rules_only, "system_error": node_system_error,
}


def _after_intake(state) -> str:
    if state.get("intake_rejection") or not _ok(state, "intake"):
        return END
    return "correlate_dedup"


def _after_correlate(state) -> str:
    return "redact" if _ok(state, "correlate_dedup") else "system_error"


def _after_pass1(state) -> Union[str, list[str]]:
    if not _ok(state, "severity_rules_pass1") or not config_llm_enabled():
        return "rules_only_dispatch"
    if state["rules_pass1"].level == SeverityLevel.S1:
        return ["dispatch_fast_page", "triage_agent"]
    return "triage_agent"


def _after_join(state) -> str:
    ok = _ok(state, "rca_agent") and _ok(state, "change_correlation_agent")
    return "recommendation_agent" if ok else "rules_only_dispatch"


def _after_recommendation(state) -> str:
    if not _ok(state, "recommendation_agent"):
        return "rules_only_dispatch"
    rca, previous = state.get("rca"), state.get("rules_pass2")
    if rca and previous and rca.severity_inputs_found and severity_rules.would_raise(
            previous, _signals(state), rca.severity_inputs_found, state.get("triage")):
        return "severity_rescore"
    return "output_guardrails"


def _after_final_gate(state) -> str:
    if not _ok(state, "final_severity_gate"):
        return "rules_only_dispatch"
    return "dispatch_page" if state["final_severity"].level.group == SeverityGroup.MAJOR else "dispatch_itsm_assign"


def _after_page(source: str):
    return lambda state: "notify" if _ok(state, source) else "rules_only_dispatch"


def _after_finalize(state) -> str:
    return END if _ok(state, "finalize") else "rules_only_dispatch"


@lru_cache(maxsize=1)
def build_graph():
    """Section 4.2 and 4.3. Built once per process."""
    graph = StateGraph(GraphState)
    for name, body in NODES.items():
        graph.add_node(name, wrap(name, body))
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _after_intake, ["correlate_dedup", END])
    graph.add_conditional_edges("correlate_dedup", _after_correlate, ["redact", "system_error"])
    graph.add_conditional_edges("redact", guarded("redact", "severity_rules_pass1"),
                                ["severity_rules_pass1", "rules_only_dispatch"])
    graph.add_conditional_edges("severity_rules_pass1", _after_pass1,
                                ["dispatch_fast_page", "triage_agent", "rules_only_dispatch"])
    graph.add_edge("dispatch_fast_page", END)
    graph.add_conditional_edges("triage_agent", guarded("triage_agent", "itsm_upsert"),
                                ["itsm_upsert", "rules_only_dispatch"])
    graph.add_conditional_edges("itsm_upsert", guarded("itsm_upsert", "severity_rules_pass2"),
                                ["severity_rules_pass2", "rules_only_dispatch"])
    graph.add_conditional_edges("severity_rules_pass2",
                                lambda s: ["rca_agent", "change_correlation_agent"] if _ok(s, "severity_rules_pass2")
                                else "rules_only_dispatch",
                                ["rca_agent", "change_correlation_agent", "rules_only_dispatch"])
    graph.add_edge(["rca_agent", "change_correlation_agent"], "analysis_join")
    graph.add_conditional_edges("analysis_join", _after_join, ["recommendation_agent", "rules_only_dispatch"])
    graph.add_conditional_edges("recommendation_agent", _after_recommendation,
                                ["severity_rescore", "output_guardrails", "rules_only_dispatch"])
    graph.add_conditional_edges("severity_rescore", guarded("severity_rescore", "output_guardrails"),
                                ["output_guardrails", "rules_only_dispatch"])
    graph.add_conditional_edges("output_guardrails", guarded("output_guardrails", "confidence_gate"),
                                ["confidence_gate", "rules_only_dispatch"])
    graph.add_edge("confidence_gate", "final_severity_gate")
    graph.add_conditional_edges("final_severity_gate", _after_final_gate,
                                ["dispatch_page", "dispatch_itsm_assign", "rules_only_dispatch"])
    graph.add_conditional_edges("dispatch_page", _after_page("dispatch_page"), ["notify", "rules_only_dispatch"])
    graph.add_conditional_edges("dispatch_itsm_assign", _after_page("dispatch_itsm_assign"),
                                ["notify", "rules_only_dispatch"])
    graph.add_conditional_edges("notify", guarded("notify", "finalize"), ["finalize", "rules_only_dispatch"])
    graph.add_conditional_edges("finalize", _after_finalize, [END, "rules_only_dispatch"])
    graph.add_edge("rules_only_dispatch", "system_error")
    graph.add_edge("system_error", END)
    return graph.compile()


# ============================================================ entry points

def run_investigation(raw_input: str = "", *, scenario_id: Optional[str] = None,
                      supplied_window: Optional[dict[str, Any]] = None, run_mode: RunMode = RunMode.LIVE,
                      prompt_version_override: Optional[dict[str, str]] = None,
                      progress_callback: Optional[Callable[[str, dict[str, Any]], None]] = None,
                      reference_time: Optional[datetime] = None, withheld_sources: Optional[list[str]] = None
                      ) -> Union[InvestigationOutput, IntakeRejection]:
    """Run one investigation (Section 4.1). An empty raw_input with a scenario_id is a detection replay."""
    from src.evaluation.langfuse_tracker import investigation_trace

    run_id = store.new_run_id()
    state = initial_state(raw_input, run_mode=run_mode, supplied_window=supplied_window, scenario_id=scenario_id,
                          prompt_version_override=prompt_version_override, run_id=run_id,
                          reference_time=reference_time, withheld_sources=withheld_sources)
    final: dict[str, Any] = dict(state)
    try:
        with investigation_trace(run_id, scenario_id, run_mode.value) as trace:
            state["langfuse_trace_id"] = trace.trace_id
            run_config = {"callbacks": trace.callbacks, "run_name": "investigation",
                          "metadata": {"run_id": run_id, "scenario_id": scenario_id}, "recursion_limit": 60}
            for mode, chunk in build_graph().stream(state, run_config, stream_mode=["updates", "values"]):
                if mode == "values":
                    final = chunk
                elif progress_callback is not None:
                    for node_name, update in (chunk or {}).items():
                        try:
                            progress_callback(node_name, update or {})
                        except Exception:  # noqa: BLE001 - the UI must not break a run
                            pass
            output = final.get("output")
            trace.finish({"status": output.status.value if output else "rejected",
                          "incident_id": output.incident_id if output else None})
    finally:
        llm_module.release_budget(run_id)
    if final.get("intake_rejection"):
        return IntakeRejection(**final["intake_rejection"])
    if final.get("output") is None:
        return IntakeRejection(reason="internal_error",
                               message="The investigation could not start; see error.log for run " + run_id)
    return final["output"]


def run_reclassification(incident_id: str, reclassification: Reclassification) -> DispatchRecord:
    """H-4 / FR-53: a human raises the incident to Major. Final severity gate, page, notify."""
    view = store.current(incident_id)
    if view is None or not view.run_ids:
        raise ValueError(f"unknown incident {incident_id}")
    run_id = view.run_ids[-1]
    path = config.get_settings().incident_state_dir / "outputs" / f"{run_id}.incident.json"
    incident = IncidentObject.model_validate_json(path.read_text(encoding="utf-8"))
    saved = store.load_output(run_id) or {}
    final = SeverityAssessment(level=reclassification.to_level,
                               rationale=f"Re-classified by {reclassification.reviewer_role.value}: "
                                         f"{reclassification.reason}",
                               flags=["human_reclassified"])
    state: dict[str, Any] = {"incident": incident, "run_mode": RunMode.LIVE, "final_severity": final,
                             "dispatch": DispatchRecord(paged=bool(view.paged_at))}
    with log_context(incident_id=incident_id, run_id=run_id):
        update = gates.page(state)
        dispatch = merge_dispatch(state["dispatch"], update.get("dispatch"))
        summary = SummaryFields(what_happened=(saved.get("stakeholder_summary") or "Incident re-classified as Major."),
                                customer_impact="Being assessed.", current_status="Escalated to Major.",
                                next_update="Within 30 minutes.")
        sent, _ = notification_service.notify(incident_id, run_id, final, summary, bool(saved.get("needs_human_rca")),
                                              incident.reported_service or "unknown")
    return merge_dispatch(dispatch, DispatchRecord(notifications=sent))

"""RCA Agent (Architecture Spec Section 6.3; Req. FR-46, FR-47).

The LLM chooses read tools (bound with bind_tools) in at most two rounds,
within RCA_TOOL_BUDGET calls; independent calls in a round run in parallel.
Tool results reach the model as compact JSON inside <untrusted_data> tags.
A final structured call returns the timeline and ranked hypotheses.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from src import config as app_config
from src.agent.agent_support import AgentRun, fmt_anomalies, fmt_evidence, fmt_incident
from src.schemas.analysis import Hypothesis, RcaResult, TimelineEvent
from src.schemas.common import EVIDENCE_ID_PATTERN, Confidence, LenientStrList
from src.schemas.evidence import EvidenceItem, ToolSummary
from src.tools.implementations._common import lookup_evidence
from src.tools.tool_registry import get_registry

AGENT = "rca_agent"
SEVERITY_SIGNALS = {"customer_error_rate_pct", "payment_route_failure_pct", "offline_partitions",
                    "under_replicated_partitions", "consumer_lag_payments", "duplicate_transaction_ids",
                    "complaints_per_product_30min"}

# Section 6.3: the recommended first tools per issue class.
STARTING_TOOLS: dict[str, list[str]] = {
    "release_regression": ["query_logs", "query_api_metrics", "query_db_infra_metrics", "get_service_dependencies"],
    "config_change": ["query_logs", "query_api_metrics", "query_db_infra_metrics", "get_service_dependencies"],
    "eventhub_broker": ["query_kafka_events", "query_cluster_quorum", "get_service_dependencies", "query_api_metrics"],
    "eventhub_connect": ["query_connect_status", "query_kafka_events", "query_logs"],
    "eventhub_acl": ["query_acl_audit", "query_logs", "query_kafka_events"],
    "eventhub_schema": ["query_schema_registry", "query_logs", "query_kafka_events"],
    "database_capacity": ["query_db_infra_metrics", "query_api_metrics", "query_logs", "get_service_dependencies"],
    "network_tls": ["query_network", "query_schema_registry", "query_logs", "query_api_metrics"],
    "data_integrity": ["query_kafka_events", "query_logs", "query_complaints"],
    "other": ["query_logs", "query_api_metrics", "get_service_dependencies", "query_complaints"],
}


class _TimelineDraft(BaseModel):
    timestamp: str = Field(description="ISO 8601 UTC time of the event")
    source: str
    service: str
    description: str
    evidence_id: str


class _HypothesisDraft(BaseModel):
    root_cause: str
    confidence: Confidence
    supporting_evidence: LenientStrList
    contradicting_or_missing_evidence: LenientStrList


class RcaDraft(BaseModel):
    """What the model returns; ranks and counters are set by the code."""

    timeline: list[_TimelineDraft]
    hypotheses: list[_HypothesisDraft] = Field(description="Most likely first, at most 3")
    confirmed_anomalies: LenientStrList
    severity_inputs_found: dict[str, float]


def _collect(result: Any, evidence: dict[str, EvidenceItem]) -> None:
    if isinstance(result, ToolSummary):
        for item in result.notable:
            evidence.setdefault(item.evidence_id, item)


def run(state: dict[str, Any], config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    a = AgentRun(AGENT, state, config)
    incident, triage = a.incident, state["triage"]
    registry = get_registry()
    budget = app_config.get_settings().rca_tool_budget
    evidence: dict[str, EvidenceItem] = {e.evidence_id: e for e in lookup_evidence(
        a.ctx, [i for e in incident.anomaly_events for i in e.evidence_ids])}
    tools = registry.as_langchain_tools(AGENT)
    starting = STARTING_TOOLS.get(triage.issue_class.value, STARTING_TOOLS["other"])
    context = "\n\n".join([
        fmt_incident(incident),
        f"Triage: issue class {triage.issue_class.value}, proposed {triage.proposed_severity.value}, affected "
        + ", ".join(f"{s.service} ({s.role.value})" for s in triage.affected_services)
        + f". Rationale: {triage.rationale}",
        "Anomaly events:\n" + fmt_anomalies(incident.anomaly_events),
        "Evidence behind the anomalies:\n" + a.untrusted(fmt_evidence(evidence.values()) or "(none)", "evidence"),
        f"Recommended starting tools for {triage.issue_class.value}: {', '.join(starting)}. "
        f"Tool budget: {budget} calls in at most 2 rounds. The incident window is used when you omit one.",
    ])
    messages: list[Any] = [SystemMessage(a.system_prompt()), HumanMessage(context)]

    used, followup = 0, False
    for round_number in (1, 2):
        reply = a.with_tools(tools, messages)
        messages.append(reply)
        calls = list(getattr(reply, "tool_calls", None) or [])
        if not calls:
            break
        followup = followup or round_number == 2
        allowed, skipped = calls[: max(0, budget - used)], calls[max(0, budget - used):]

        def execute(call: dict[str, Any]):
            return a.retry(lambda: registry.call_for_llm(call["name"], call.get("args") or {}, AGENT, a.ctx),
                           call["name"])

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(execute, allowed))
        for call, (content, flags, result) in zip(allowed, outcomes):
            a.flags.extend(f for f in flags if f not in a.flags)
            _collect(result, evidence)
            messages.append(ToolMessage(content=a.untrusted(content, call["name"]), tool_call_id=call["id"]))
        for call in skipped:
            messages.append(ToolMessage(content="Not run: the tool-call budget is used up.", tool_call_id=call["id"]))
        used += len(allowed)
        if used >= budget:
            break

    messages.append(HumanMessage("Now give your RCA result, citing only evidence IDs from this conversation."))
    draft = a.structured(RcaDraft, messages)

    known_anomalies = {e.anomaly_id for e in incident.anomaly_events}
    timeline = []
    for event in draft.timeline[:12]:
        item = evidence.get(event.evidence_id)
        if item is None:
            continue  # cites nothing we have: dropped (grounding)
        timeline.append(TimelineEvent(timestamp=item.timestamp, source=item.source.value, service=event.service,
                                      description=event.description, evidence_id=event.evidence_id))
    timeline.sort(key=lambda t: t.timestamp)
    hypotheses = [Hypothesis(rank=i, root_cause=h.root_cause, confidence=h.confidence,
                             supporting_evidence=[e for e in dict.fromkeys(h.supporting_evidence) if re.match(EVIDENCE_ID_PATTERN, e)],
                             contradicting_or_missing_evidence=h.contradicting_or_missing_evidence)
                  for i, h in enumerate(sorted(draft.hypotheses, key=lambda h: -h.confidence)[:3], start=1)]
    result = RcaResult(
        timeline=timeline, hypotheses=hypotheses,
        confirmed_anomalies=[x for x in draft.confirmed_anomalies if x in known_anomalies],
        tool_calls_used=used, followup_used=followup,
        severity_inputs_found={k: float(v) for k, v in draft.severity_inputs_found.items() if k in SEVERITY_SIGNALS},
    )
    return {"rca": result, "evidence": evidence, **a.common_update()}

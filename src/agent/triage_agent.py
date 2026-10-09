"""Triage Agent (Architecture Spec Section 6.2; Req. FR-40).

Before the LLM call the node itself gathers complaints, the dependency map
and runbooks; the LLM then makes one structured call that returns the issue
class, a proposed severity, affected services and supporting evidence.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.agent_support import (
    AgentRun, fmt_anomalies, fmt_documents, fmt_evidence, fmt_incident, fmt_rules, tool_text,
)
from src.schemas.analysis import TriageResult
from src.tools.implementations._common import lookup_evidence
from src.tools.implementations.get_service_dependencies import load_dependency_map

AGENT = "triage_agent"


def run(state: dict[str, Any], config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    a = AgentRun(AGENT, state, config)
    incident = a.incident
    anomaly_ids = [i for e in incident.anomaly_events for i in e.evidence_ids]
    evidence = {e.evidence_id: e for e in lookup_evidence(a.ctx, anomaly_ids)}

    complaints = a.tool("query_complaints", {})
    evidence.update({e.evidence_id: e for e in complaints.notable})
    deps = None
    if incident.reported_service in load_dependency_map():
        deps = a.tool("get_service_dependencies", {"service": incident.reported_service})
    signals = " ".join(sorted({f"{e.service} {e.signal.split(':')[0].replace('_', ' ')}"
                               for e in incident.anomaly_events}))
    query = f"{incident.raw_input} {signals}".strip()[:600] or "incident symptoms"
    docs = a.tool("search_knowledge_base", {"query": query, "top_k": 4,
                                            "doc_types": ["runbook", "verified_resolution"]})

    context = "\n\n".join([
        fmt_incident(incident),
        "Reported input:\n" + a.untrusted(incident.raw_input or "(detection replay, no text)", "input"),
        "Rules-based severity, first pass (context only; the rules decide the final severity):\n"
        + fmt_rules(state.get("rules_pass1")),
        "Anomaly events:\n" + fmt_anomalies(incident.anomaly_events),
        "Evidence records:\n" + a.untrusted(fmt_evidence(evidence.values()) or "(none)", "evidence"),
        "Customer complaints (query_complaints):\n" + a.untrusted(tool_text(complaints), "query_complaints"),
        "Service dependencies:\n" + (tool_text(deps) if deps else "(service unknown)"),
        "Runbooks and verified resolutions:\n" + a.untrusted(fmt_documents(docs), "search_knowledge_base"),
    ])
    result = a.structured(TriageResult, [SystemMessage(a.system_prompt()), HumanMessage(context)])
    known = set(evidence)
    result = result.model_copy(update={"evidence_ids": [e for e in result.evidence_ids if e in known]})
    return {"triage": result, "evidence": evidence, "retrieved_documents": docs, **a.common_update()}

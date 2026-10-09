"""Change Correlation Agent (Architecture Spec Section 6.4; Req. FR-12, FR-45).

Pre-step without LLM: change records from CHANGE_LOOKBACK_MIN before the onset
to the window end, with minutes before onset and whether each change is on the
dependency path of the affected services. The LLM only ranks and explains;
every factual field of a finding comes from the pre-step.
"""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src import config as app_config
from src.agent.agent_support import AgentRun, fmt_incident
from src.schemas.analysis import ChangeCorrelationResult, ChangeFinding
from src.schemas.enums import ChangeType
from src.tools.implementations.get_service_dependencies import load_dependency_map

AGENT = "change_correlation_agent"


class _Ranked(BaseModel):
    change_id: str
    contributes: bool = Field(description="True if this change plausibly caused or contributed to the incident")
    rationale: str


class ChangeRanking(BaseModel):
    findings: list[_Ranked] = Field(description="Changes in order of likelihood, most likely first")


def onset_of(incident) -> Any:
    return min((e.detected_at for e in incident.anomaly_events), default=incident.reference_time)


def run(state: dict[str, Any], config: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    a = AgentRun(AGENT, state, config)
    incident, triage = a.incident, state["triage"]
    lookback = app_config.get_settings().change_lookback_min
    onset = onset_of(incident)
    start = min(onset, incident.window_start)
    # Searched range: from `lookback` minutes before the earlier of onset and window start, to the window end.
    summary = a.tool("query_change_records", {"window_start": start.isoformat(),
                                              "window_end": incident.window_end.isoformat(), "lookback_min": lookback})
    searched = int((incident.window_end - start).total_seconds() // 60) + lookback
    evidence = {item.evidence_id: item for item in summary.notable}
    if summary.source_unavailable or not summary.notable:
        return {"changes": ChangeCorrelationResult(findings=[], searched_window_min=searched), "evidence": evidence,
                **a.common_update()}

    deps = load_dependency_map()
    affected = {s.service for s in triage.affected_services} | {incident.reported_service or ""}
    # The causal path is the failing (origin) service and what it depends on; services that only show
    # symptoms (customer channels, callers) are downstream of the fault, not on its path.
    origins = {s.service for s in triage.affected_services if s.role.value == "origin"} or {
        incident.reported_service or ""}
    on_path: set[str] = set(origins)
    for service in origins:
        if service in deps:
            on_path.update(a.tool("get_service_dependencies", {"service": service, "direction": "upstream"}).upstream)

    candidates = {}
    lines = []
    for item in summary.notable:
        record = item.record
        minutes = round((onset - item.timestamp).total_seconds() / 60, 1)
        candidates[item.evidence_id] = (record, minutes, item.service in on_path)
        lines.append(f"{item.evidence_id}: {record.get('change_type')} on {item.service}, {minutes:g} min before "
                     f"onset, {'on' if item.service in on_path else 'not on'} the dependency path. "
                     f"{record.get('change_summary', '')}")
    context = "\n\n".join([
        fmt_incident(incident),
        f"Onset: {onset:%Y-%m-%d %H:%M}Z. Triage: {triage.issue_class.value}; affected services: "
        + ", ".join(sorted(s for s in affected if s)) + f". {triage.rationale}",
        "Change records:\n" + a.untrusted("\n".join(lines), "query_change_records"),
    ])
    ranking = a.structured(ChangeRanking, [SystemMessage(a.system_prompt()), HumanMessage(context)])

    findings, seen = [], set()
    for ranked in ranking.findings:
        if not ranked.contributes or ranked.change_id not in candidates or ranked.change_id in seen:
            continue
        seen.add(ranked.change_id)
        record, minutes, path = candidates[ranked.change_id]
        findings.append(ChangeFinding(change_id=ranked.change_id, change_type=ChangeType(record["change_type"]),
                                      service=record["service"], time_before_onset_min=minutes,
                                      on_dependency_path=path, rationale=ranked.rationale))
    return {"changes": ChangeCorrelationResult(findings=findings, searched_window_min=searched), "evidence": evidence,
            **a.common_update()}

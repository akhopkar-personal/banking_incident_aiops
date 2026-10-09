"""Rules-only investigation of a replayed scenario (Req. FR-43, FR-68).

Runs the deterministic part of the workflow without any LLM: detection,
correlation and dedup, the Incident Object, redaction, severity pass 1, the
critical fast-path page, then the rules-only dispatch (ticket, and page or
assignment). This is what the graph does when the kill switch is on or an LLM
node fails; Phase 4's graph calls the same service functions node by node.

CLI: python scripts/run_rules_only.py SC-01 [--evaluation] [--withhold DEP]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

from src import config
from src.logger_setup import log_context, log_interaction
from src.safety.injection_detector import scan
from src.safety.redaction import redact
from src.schemas.analysis import RulesResult
from src.schemas.enums import InputMode, RunMode
from src.schemas.graph_state import merge_dispatch
from src.schemas.incident import IncidentObject
from src.schemas.output import DispatchRecord
from src.services import alert_correlator, anomaly_detector, gates, severity_rules
from src.services import incident_state_store as store
from src.tools.implementations._common import parse_time


@dataclass
class RulesOnlyResult:
    scenario_id: str
    run_id: str
    incident_id: Optional[str] = None
    created: bool = False
    deduplicated: bool = False
    root_service: Optional[str] = None
    idempotency_key: Optional[str] = None
    anomaly_count: int = 0
    groups: int = 0
    rules_pass1: Optional[RulesResult] = None
    dispatch: DispatchRecord = field(default_factory=DispatchRecord)
    guardrail_flags: list[str] = field(default_factory=list)
    incident: Optional[IncidentObject] = None

    def summary(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id, "run_id": self.run_id, "incident_id": self.incident_id,
            "created": self.created, "deduplicated": self.deduplicated, "root_service": self.root_service,
            "anomaly_events": self.anomaly_count, "incident_groups": self.groups,
            "severity": self.rules_pass1.level.value if self.rules_pass1 else None,
            "rules_fired": [r.rule_id for r in self.rules_pass1.rules_fired] if self.rules_pass1 else [],
            "flags": self.rules_pass1.flags if self.rules_pass1 else [],
            "dispatch": self.dispatch.model_dump(mode="json", exclude_none=True),
            "guardrail_flags": self.guardrail_flags,
        }


def scenario_manifest(scenario_id: str) -> dict[str, Any]:
    path = config.get_settings().scenario_dir(scenario_id) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_rules_only(scenario_id: str, *, window_start: Optional[datetime] = None,
                   window_end: Optional[datetime] = None, reference_time: Optional[datetime] = None,
                   run_mode: RunMode = RunMode.LIVE, withheld_sources: Iterable[str] = (), raw_input: str = "",
                   input_mode: InputMode = InputMode.DETECTION_REPLAY) -> RulesOnlyResult:
    manifest = scenario_manifest(scenario_id)
    start = window_start or parse_time(manifest["window_start"])
    end = window_end or parse_time(manifest["window_end"])
    reference = reference_time or parse_time(manifest["reference_time"])
    withheld = tuple(withheld_sources)
    run_id = store.new_run_id()
    result = RulesOnlyResult(scenario_id=scenario_id, run_id=run_id)

    with log_context(run_id=run_id):
        events = anomaly_detector.detect(scenario_id, start, end, withheld)
        groups = alert_correlator.group(events)
        primary = alert_correlator.primary_group(groups)
        result.anomaly_count, result.groups = len(events), len(groups)
        if primary is None:
            return result
        incident_id, created = alert_correlator.upsert_incident(primary, reference_time=reference, run_id=run_id,
                                                                scenario_id=scenario_id)
        result.incident_id, result.created, result.deduplicated = incident_id, created, not created
        result.root_service, result.idempotency_key = primary.root_service, primary.idempotency_key
        if not created:
            return result  # FR-38: a repeat updates nothing and dispatches nothing

        with log_context(incident_id=incident_id):
            text, pii_flags = redact(raw_input)
            flags = [f"redacted_{f}" for f in pii_flags] + scan(text)
            incident = IncidentObject(
                incident_id=incident_id, run_id=run_id, idempotency_key=primary.idempotency_key, input_mode=input_mode,
                scenario_id=scenario_id, raw_input=text, anomaly_events=primary.events,
                reported_service=primary.root_service, reference_time=reference, window_start=start,
                window_end=end, withheld_sources=list(withheld))
            if not config.get_settings().llm_enabled:
                log_interaction("kill_switch_active", component="rules_only_runner")
            signals = severity_rules.compute_signals(scenario_id, start, end, withheld)
            pass1 = severity_rules.evaluate("pass1", signals)
            severity_rules.log_result(pass1, "severity_rules_pass1")
            state: dict[str, Any] = {"incident": incident, "run_mode": run_mode, "rules_pass1": pass1,
                                     "dispatch": DispatchRecord()}
            for step in (gates.fast_page, gates.rules_only):
                update = step(state)
                if update.get("dispatch") is not None:
                    state["dispatch"] = merge_dispatch(state["dispatch"], update["dispatch"])
            result.incident, result.rules_pass1 = incident, pass1
            result.dispatch, result.guardrail_flags = state["dispatch"], flags
    return result

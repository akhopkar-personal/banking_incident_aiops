"""Shared plumbing for the four agents: the run context, tool calls with
retries, prompt formatting with untrusted-data delimiters, and injection flags."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from src.agent import llm, prompts
from src.safety.injection_detector import scan, wrap_untrusted
from src.schemas.analysis import RetrievedDocument
from src.schemas.evidence import EvidenceItem
from src.schemas.incident import AnomalyEvent
from src.tools.implementations import ToolContext
from src.tools.tool_registry import compact_json, get_registry


@dataclass
class AgentRun:
    """One agent's view of the run."""

    agent: str
    state: dict[str, Any]
    config: Optional[dict[str, Any]] = None
    flags: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        incident = self.state["incident"]
        self.incident = incident
        self.ctx = ToolContext(scenario_id=incident.scenario_id, incident_id=incident.incident_id,
                               run_id=incident.run_id, withheld_sources=tuple(incident.withheld_sources),
                               window_start=incident.window_start, window_end=incident.window_end)
        self.version = prompts.resolve_version(self.agent, self.state.get("prompt_version_override"))
        self.llm = llm.LlmCall(agent=self.agent, incident_id=incident.incident_id, run_id=incident.run_id,
                               llm_available=self.state.get("llm_available", True), parent_config=self.config)

    # --- calls

    def retry(self, fn, what: str):
        return llm.with_retries(fn, what, self.agent, self.incident.incident_id, self.incident.run_id)

    def tool(self, name: str, args: dict[str, Any]) -> Any:
        return self.retry(lambda: get_registry().call(name, args, self.agent, self.ctx), name)

    def structured(self, schema, messages):
        result, usage = self.retry(lambda: self.llm.structured(schema, messages), f"{schema.__name__} call")
        self._add_usage(usage)
        return result

    def with_tools(self, tools, messages):
        message, usage = self.retry(lambda: self.llm.with_tools(tools, messages), "tool-calling call")
        self._add_usage(usage)
        return message

    def _add_usage(self, usage: dict[str, int]) -> None:
        for key, value in usage.items():
            self.token_usage[key] = self.token_usage.get(key, 0) + value

    # --- prompt text

    def system_prompt(self) -> str:
        return prompts.render(self.agent, self.version)

    def untrusted(self, text: str, source: str) -> str:
        """Delimit data for the prompt, and flag it if it looks like an injected instruction."""
        for flag in scan(text):
            if flag not in self.flags:
                self.flags.append(flag)
        return wrap_untrusted(text, source)

    def common_update(self) -> dict[str, Any]:
        return {"prompt_version_set": {self.agent: self.version}, "token_usage": self.token_usage,
                "guardrail_flags": list(self.flags)}


# ------------------------------------------------------------- formatting

def fmt_evidence(items: Iterable[EvidenceItem]) -> str:
    return "\n".join(f"[{e.evidence_id}] {e.timestamp:%Y-%m-%d %H:%M:%S}Z {e.service}: {e.summary}" for e in items)


def fmt_anomalies(events: Iterable[AnomalyEvent]) -> str:
    return "\n".join(
        f"{e.anomaly_id} {e.detected_at:%H:%M}Z {e.service} {e.signal}: observed {e.observed:g}, baseline "
        f"{e.baseline:g}{f', z {e.z_score:g}' if e.z_score is not None else ''} ({e.method}); evidence "
        f"{', '.join(e.evidence_ids[:3])}" for e in events) or "(none)"


def fmt_documents(docs: Iterable[RetrievedDocument]) -> str:
    return "\n\n".join(f"{d.doc_id} | {d.section or ''} | {d.title}"
                       f"{' | STALE' if d.stale else ''}\n{d.excerpt}" for d in docs) or "(none)"


def fmt_rules(rules) -> str:
    if rules is None:
        return "(not evaluated)"
    fired = ", ".join(f"{r.rule_id} (observed {r.observed:g}, threshold {r.threshold:g})" for r in rules.rules_fired)
    flags = f"; flags: {', '.join(rules.flags)}" if rules.flags else ""
    return f"{rules.level.value} ({rules.group.value}) from rules: {fired or 'none fired'}{flags}"


def fmt_incident(incident) -> str:
    return "\n".join([
        f"Incident {incident.incident_id}, input mode {incident.input_mode.value}",
        f"Window: {incident.window_start:%Y-%m-%d %H:%M}Z to {incident.window_end:%H:%M}Z; reference time "
        f"{incident.reference_time:%H:%M}Z",
        f"Reported or root service: {incident.reported_service or 'unknown'}",
        f"Unavailable data sources: {', '.join(incident.withheld_sources) or 'none'}",
    ])


def tool_text(result: Any) -> str:
    return compact_json(result)


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

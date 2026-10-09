"""A scripted stand-in for ChatOpenAI, for offline graph tests.

It supports the two calls the agents make: `bind_tools(...).invoke(messages)`
and `with_structured_output(schema, method=..., include_raw=True).invoke(messages)`.
`ScenarioOracle` answers like a competent model for one scenario, using the
draft ground truth, but it can only cite evidence IDs that appear in the
conversation, so grounding is exercised for real.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage

from data.generators import SCENARIOS
from src.agent.change_correlation_agent import ChangeRanking
from src.agent.rca_agent import STARTING_TOOLS, RcaDraft
from src.schemas.analysis import RecommendationResult, TriageResult
from tests.data_helpers import manifest

USAGE = {"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500}
EVIDENCE = re.compile(r"\b(?:LOG|KFK|API|DBM|NET|DEP|CMP|CON|ACL|SRG|QRM)-\d{4}\b")


def text_of(messages: list[Any]) -> str:
    """The conversation without the system prompt (a model cites what it was shown as data)."""
    return "\n".join(str(getattr(m, "content", m)) for m in messages if getattr(m, "type", "") != "system")


class FakeChatModel:
    def __init__(self, answer: Callable[[str, Any, list[Any]], Any], fail_with: Optional[Exception] = None,
                 delay_s: float = 0.0, finish_reason: str = "stop"):
        self.answer = answer
        self.fail_with = fail_with
        self.delay_s = delay_s
        self.finish_reason = finish_reason
        self.calls: list[str] = []
        self.finished_at: list[datetime] = []  # when each call returned (UTC)

    def _done(self) -> None:
        if self.delay_s:
            time.sleep(self.delay_s)
        self.finished_at.append(datetime.now(timezone.utc))

    def with_structured_output(self, schema, method=None, include_raw=False):
        assert method == "function_calling" and include_raw
        model = self

        class Structured:
            def invoke(self, messages, config=None):
                model.calls.append(schema.__name__)
                if model.fail_with:
                    raise model.fail_with
                parsed = model.answer("structured", schema, messages)
                raw = AIMessage(content="", usage_metadata=USAGE,
                                response_metadata={"finish_reason": model.finish_reason, "model_name": "fake"})
                model._done()
                return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return Structured()

    def bind_tools(self, tools):
        model = self

        class Bound:
            def invoke(self, messages, config=None):
                model.calls.append("tools")
                if model.fail_with:
                    raise model.fail_with
                reply = model.answer("tools", tools, messages)
                model._done()
                return reply

        return Bound()


class ScenarioOracle:
    """Answers for one scenario. `confidence` sets the top hypothesis confidence."""

    def __init__(self, scenario_id: str, confidence: float = 0.8, propose: Optional[str] = None,
                 severity_inputs: Optional[dict[str, float]] = None):
        self.s = SCENARIOS[scenario_id]
        self.m = manifest(scenario_id)
        self.confidence = confidence
        self.severity_inputs = severity_inputs or {}
        self.propose = propose or self.s.expected_severity
        self.tool_round = 0

    def __call__(self, kind: str, target: Any, messages: list[Any]) -> Any:
        text = text_of(messages)
        seen = list(dict.fromkeys(EVIDENCE.findall(text)))
        if kind == "tools":
            self.tool_round += 1
            if any(getattr(m, "type", "") == "tool" for m in messages):
                return AIMessage(content="Enough evidence.", usage_metadata=USAGE)
            names = STARTING_TOOLS[self.s.issue_class]
            return AIMessage(content="", usage_metadata=USAGE, tool_calls=[
                {"name": n, "args": {"service": self._root()} if n == "get_service_dependencies"
                 else {}, "id": f"call_{i}", "type": "tool_call"} for i, n in enumerate(names)])
        if target is TriageResult:
            return TriageResult(issue_class=self.s.issue_class, proposed_severity=self.propose,
                                affected_services=[{"service": self._root(), "role": "origin"}],
                                confidence=0.8, evidence_ids=seen[:3], rationale="Fits the anomalies.")
        if target is RcaDraft:
            cite = [e for e in self.m["must_cite_evidence_ids"] if e in seen] or [e for e in seen if not e.startswith("CMP")][:2]
            return RcaDraft(
                timeline=[{"timestamp": "", "source": e[:3], "service": self._root(), "description": f"event {e}",
                           "evidence_id": e} for e in seen[:5]],
                hypotheses=[{"root_cause": self.s.root_cause, "confidence": self.confidence,
                             "supporting_evidence": cite, "contradicting_or_missing_evidence": []},
                            {"root_cause": "Unrelated noise", "confidence": 0.1, "supporting_evidence": seen[:1],
                             "contradicting_or_missing_evidence": ["weak"]}],
                confirmed_anomalies=["ANM-1"], severity_inputs_found=self.severity_inputs)
        if target is ChangeRanking:
            causal = self.m.get("causal_change_id")
            ids = [e for e in seen if e.startswith("DEP-")]
            return ChangeRanking(findings=[{"change_id": i, "contributes": i == causal, "rationale": "timing"}
                                           for i in ids])
        if target is RecommendationResult:
            shown = re.findall(r"\b(RB-[A-Z]{2,4}-\d{3}) \|", text)  # documents listed in the prompt
            runbook = self.s.runbook_id if self.s.runbook_id in shown else (shown[0] if shown else self.s.runbook_id)
            actions = {"RB-PAY-003": "rollback_release", "RB-KFK-001": "scale_out", "RB-DB-002": "reschedule_batch_job",
                       "RB-NET-004": "renew_certificate", "RB-PAY-005": "revert_config_change"}
            return RecommendationResult(
                root_cause_summary=self.s.root_cause, top_confidence=0.99,
                recommended_actions=[
                    {"step": 1, "action": "Delete the topic to clear it", "action_type": "delete_topic",
                     "risk_level": "low", "runbook_citation": f"{runbook} §2", "expected_effect": "x"},
                    {"step": 2, "action": "Apply the runbook fix", "action_type": actions.get(runbook, "investigate_further"),
                     "risk_level": "low", "runbook_citation": f"{runbook} §2", "expected_effect": "Recovery"}],
                summary_fields={"what_happened": "A service is failing.", "customer_impact": "Some customers are "
                                "affected.", "current_status": "Mitigation in progress.", "next_update": "In 30 min."},
                regulatory_notes="Report under REG-001." if self.s.regulatory else "",
                insufficient_evidence_reason="", cited_doc_ids=[runbook], cited_evidence_ids=seen[:2])
        raise AssertionError(f"unexpected call {kind} {target}")

    def _root(self) -> str:
        roots = {"SC-01": "payments-service", "SC-02": "kafka-platform", "SC-03": "core-banking-db",
                 "SC-04": "payment-network-gateway", "SC-05": "payments-service"}
        return roots[self.s.scenario_id]

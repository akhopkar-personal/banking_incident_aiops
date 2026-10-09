"""Guardrail orchestration (Architecture Spec Section 9.1).

`run_output_guardrails` runs the output checks in order for the
`output_guardrails` graph node; `check_adaptation_scope` enforces what an
adaptation may change (Section 1.3, invariant 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from src.safety import action_policy, output_checks
from src.schemas.analysis import RcaResult, RecommendationResult
from src.schemas.output import RecommendedAction


@dataclass
class GuardrailResult:
    recommendation: RecommendationResult
    rca: Optional[RcaResult]
    recommended_actions: list[RecommendedAction]
    stakeholder_summary: str
    flags: list[str] = field(default_factory=list)
    insufficient_evidence: bool = False
    action_policy_version: str = ""


def render_summary(recommendation: RecommendationResult) -> str:
    s = recommendation.summary_fields
    return " ".join(part.strip() for part in (s.what_happened, s.customer_impact, s.current_status, s.next_update)
                    if part.strip())


def _evidence_numbers(state: dict[str, Any]) -> set[str]:
    """Numbers that appear in the collected evidence: the only impact numbers a summary may state."""
    numbers: set[str] = set()
    for item in (state.get("evidence") or {}).values():
        numbers.update(re.findall(r"\d[\d,]*(?:\.\d+)?", item.summary))
        numbers.update(str(v) for v in item.record.values() if isinstance(v, (int, float)))
    return {n.replace(",", "") for n in numbers}


def run_output_guardrails(state: dict[str, Any]) -> GuardrailResult:
    """Schema, grounding, action policy, tone and PII checks, in that order (Section 9.4)."""
    recommendation, flags = output_checks.validate_schema(RecommendationResult, state.get("recommendation"))
    if recommendation is None:
        raise ValueError("recommendation failed schema validation")  # the node wrapper retries the agent once
    recommendation, rca, grounding_flags, insufficient = output_checks.check_grounding(
        recommendation, state.get("rca"), (state.get("evidence") or {}).keys(), state.get("retrieved_documents") or [])
    flags += grounding_flags

    policy = action_policy.load_policy()
    actions, policy_flags = action_policy.check(recommendation.recommended_actions, policy)
    flags += policy_flags
    if not actions:
        insufficient = True

    summary, tone_flags = output_checks.check_tone(render_summary(recommendation), _evidence_numbers(state))
    flags += tone_flags

    (recommendation, rca, actions, summary), pii_flags = output_checks.recheck_pii(
        (recommendation.model_dump(mode="json"), rca.model_dump(mode="json") if rca else None,
         [a.model_dump(mode="json") for a in actions], summary))
    flags += pii_flags
    return GuardrailResult(
        recommendation=RecommendationResult.model_validate(recommendation),
        rca=RcaResult.model_validate(rca) if rca else None,
        recommended_actions=[RecommendedAction.model_validate(a) for a in actions],
        stakeholder_summary=summary,
        flags=list(dict.fromkeys(flags)),
        insufficient_evidence=insufficient,
        action_policy_version=policy.version,
    )


# ------------------------------------------------------------ adaptation scope

ALLOWED_ADAPTATION_TYPES = {"prompt_rule", "few_shot_example", "retrieval_alias"}
ADAPTABLE_AGENTS = {"triage_agent", "rca_agent", "change_correlation_agent", "recommendation_agent"}
_FORBIDDEN_KEYS = {"model", "llm_model", "temperature", "llm_temperature", "rules", "severity_rules", "thresholds",
                   "graph", "edges", "nodes", "source_code", "code", "weights", "action_policy", "tools"}


def check_adaptation_scope(change: dict[str, Any]) -> tuple[bool, str]:
    """(allowed, reason). An adaptation may only add a prompt guideline, a few-shot example or a
    retrieval alias for one of the four agents (Req. Section 14, layer 5)."""
    kind = change.get("type")
    if kind not in ALLOWED_ADAPTATION_TYPES:
        return False, f"change type {kind!r} is outside the adaptation scope"
    forbidden = _FORBIDDEN_KEYS & set(change)
    if forbidden:
        return False, f"change touches {sorted(forbidden)}, which adaptation may not modify"
    if kind != "retrieval_alias" and change.get("agent") not in ADAPTABLE_AGENTS:
        return False, f"agent {change.get('agent')!r} cannot be adapted"
    target = str(change.get("target_file", ""))
    if target and not target.replace("\\", "/").endswith(("data/prompt_versions.json", "knowledge/retrieval_aliases.json")):
        return False, f"adaptation may only write data/prompt_versions.json or knowledge/retrieval_aliases.json"
    return True, "within scope"

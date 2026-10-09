"""Triage, rules, RCA, change correlation and recommendation results
(Architecture Spec Sections 3.6, 3.7).

LLM-facing models follow Section 6.1: text fields that may have nothing to say
are required strings (empty string when there is nothing), not optional fields.
"""

from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import AnomalyId, Confidence, EvidenceId, LenientStrList, Schema, UtcDatetime
from .enums import (
    ChangeType,
    IssueClass,
    RiskLevel,
    ServiceRole,
    SeverityGroup,
    SeverityLevel,
)


class AffectedService(Schema):
    service: str
    role: ServiceRole


class TriageResult(Schema):
    issue_class: IssueClass
    proposed_severity: SeverityLevel
    affected_services: list[AffectedService]
    confidence: Confidence
    evidence_ids: LenientStrList  # filtered to known evidence IDs by the agent
    rationale: str


class FiredRule(Schema):
    rule_id: str
    observed: float
    threshold: float
    severity: Optional[SeverityLevel] = None
    flags: list[str] = Field(default_factory=list)


class RulesResult(Schema):
    pass_name: Literal["pass1", "pass2", "rescore"]
    level: SeverityLevel
    group: SeverityGroup
    rules_fired: list[FiredRule] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    rules_version: str
    evaluated_at: UtcDatetime

    @model_validator(mode="after")
    def _group_matches_level(self) -> "RulesResult":
        if self.group != self.level.group:
            raise ValueError(f"group {self.group.value} does not match level {self.level.value}")
        return self


class TimelineEvent(Schema):
    timestamp: UtcDatetime
    source: str
    service: str
    description: str
    evidence_id: EvidenceId


class Hypothesis(Schema):
    rank: int = Field(ge=1)
    root_cause: str
    confidence: Confidence
    supporting_evidence: list[EvidenceId]
    contradicting_or_missing_evidence: LenientStrList


class RcaResult(Schema):
    timeline: list[TimelineEvent] = Field(default_factory=list)
    confirmed_anomalies: list[AnomalyId] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    tool_calls_used: int = Field(default=0, ge=0)
    followup_used: bool = False
    severity_inputs_found: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ranks_are_sequential(self) -> "RcaResult":
        ranks = [h.rank for h in self.hypotheses]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError(f"hypothesis ranks must be 1..n in order, got {ranks}")
        return self


class ChangeFinding(Schema):
    change_id: EvidenceId
    change_type: ChangeType
    service: str
    time_before_onset_min: float
    on_dependency_path: bool
    linked_hypothesis_rank: Optional[int] = Field(default=None, ge=1)
    rationale: str


class ChangeCorrelationResult(Schema):
    findings: list[ChangeFinding] = Field(default_factory=list)
    searched_window_min: int = Field(ge=0)


class RetrievedDocument(Schema):
    doc_id: str
    section: Optional[str] = None
    title: str
    doc_type: Literal[
        "runbook", "postmortem", "verified_resolution", "service_doc", "regulatory", "auto_postmortem"
    ]
    score: float
    excerpt: str
    citation_status: Literal["verified", "unverified"] = "verified"
    last_verified: Optional[date] = None
    stale: bool = False


class SummaryFields(Schema):
    what_happened: str
    customer_impact: str
    current_status: str
    next_update: str


class ProposedAction(Schema):
    """An action as proposed by the Recommendation Agent, before the action policy
    turns it into a RecommendedAction (Architecture Spec Section 9.3)."""

    step: int = Field(ge=1)
    action: str
    action_type: str
    risk_level: RiskLevel
    runbook_citation: str
    expected_effect: str


class RecommendationResult(Schema):
    root_cause_summary: str
    top_confidence: Confidence
    recommended_actions: list[ProposedAction]
    summary_fields: SummaryFields
    regulatory_notes: str
    insufficient_evidence_reason: str
    cited_doc_ids: LenientStrList
    cited_evidence_ids: LenientStrList  # grounding keeps only known evidence IDs

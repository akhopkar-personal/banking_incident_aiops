"""Investigation output (Architecture Spec Sections 3.8, 3.9; Req. Section 8.1)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field, field_validator, model_validator

from .analysis import AffectedService, ChangeFinding, FiredRule, Hypothesis, RulesResult, TimelineEvent
from .common import IncidentId, RunId, Schema, UtcDatetime
from .enums import (
    ErrorType,
    FixOutcome,
    IncidentState,
    InvestigationStatus,
    IssueClass,
    ReviewerRole,
    RiskLevel,
    RunMode,
    SeverityLevel,
)
from .evidence import EvidenceItem


class SeverityAssessment(Schema):
    level: SeverityLevel
    rationale: str
    source: Literal["rules"] = "rules"
    llm_proposed_level: Optional[SeverityLevel] = None
    rules_fired: list[FiredRule] = Field(default_factory=list)
    rescored: bool = False
    flags: list[str] = Field(default_factory=list)


class RecommendedAction(Schema):
    step: int = Field(ge=1)
    action: str
    action_type: str
    risk_level: RiskLevel
    runbook_citation: str
    expected_effect: str
    requires_approval: bool = True
    policy_flags: list[str] = Field(default_factory=list)

    @field_validator("requires_approval")
    @classmethod
    def _always_requires_approval(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("requires_approval must always be true (Req. 8.1)")
        return value


class Notification(Schema):
    channel: Literal["chat", "email"]
    audience: str
    time: UtcDatetime
    outbox_id: str


class DispatchRecord(Schema):
    paged: bool = False
    fast_path: bool = False
    page_time: Optional[UtcDatetime] = None
    itsm_ticket_id: Optional[str] = None
    assigned_queue: Optional[str] = None
    notifications: list[Notification] = Field(default_factory=list)
    suppressed: bool = False
    intended: dict[str, Any] = Field(default_factory=dict)


class ErrorDetail(Schema):
    failed_node: str
    error_type: ErrorType
    retry_count: int = Field(ge=0)
    message: str


class OutputMetadata(Schema):
    model: Optional[str] = None
    token_counts: dict[str, int] = Field(default_factory=dict)
    cost_estimate_usd: Optional[float] = Field(default=None, ge=0.0)
    latency_ms_per_stage: dict[str, int] = Field(default_factory=dict)
    guardrail_flags: list[str] = Field(default_factory=list)
    timezone_assumed_utc: bool = False
    langfuse_trace_id: Optional[str] = None
    prompt_version_set: dict[str, str] = Field(default_factory=dict)
    rules_version: Optional[str] = None
    action_policy_version: Optional[str] = None
    run_mode: RunMode = RunMode.LIVE
    reward_score_at_run: Optional[float] = None


class ResolutionInfo(Schema):
    actual_root_cause: str
    actions_taken: str
    fix_outcome: FixOutcome
    verified_by: Optional[ReviewerRole] = None
    verified_at: Optional[UtcDatetime] = None


# Fields that make up the recommendation body. A SYSTEM_ERROR result has none of
# them: they are absent from the serialized output, not empty or null.
BODY_FIELDS = (
    "issue_class", "severity", "affected_services", "timeline", "hypotheses",
    "change_findings", "evidence", "recommended_actions", "needs_human_rca",
    "stakeholder_summary", "regulatory_notes", "insufficient_evidence_reason",
)


class InvestigationOutput(Schema):
    incident_id: IncidentId
    run_id: RunId
    status: InvestigationStatus
    incident_state: IncidentState

    issue_class: Optional[IssueClass] = None
    severity: Optional[SeverityAssessment] = None
    affected_services: list[AffectedService] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    change_findings: list[ChangeFinding] = Field(default_factory=list)
    evidence: dict[str, EvidenceItem] = Field(default_factory=dict)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    needs_human_rca: bool = False
    stakeholder_summary: Optional[str] = None
    regulatory_notes: Optional[str] = None
    insufficient_evidence_reason: Optional[str] = None

    dispatch: Optional[DispatchRecord] = None
    rules_severity: Optional[RulesResult] = None
    error_detail: Optional[ErrorDetail] = None
    resolution: Optional[ResolutionInfo] = None
    metadata: OutputMetadata = Field(default_factory=OutputMetadata)

    @model_validator(mode="after")
    def _status_rules(self) -> "InvestigationOutput":
        status = self.status
        if status == InvestigationStatus.SYSTEM_ERROR:
            if self.error_detail is None:
                raise ValueError("SYSTEM_ERROR requires error_detail")
            present = [
                name for name in ("issue_class", "severity", "stakeholder_summary",
                                  "regulatory_notes", "insufficient_evidence_reason")
                if getattr(self, name) is not None
            ] + [
                name for name in ("affected_services", "timeline", "hypotheses",
                                  "change_findings", "evidence", "recommended_actions")
                if getattr(self, name)
            ]
            if self.needs_human_rca:
                present.append("needs_human_rca")
            if present:
                raise ValueError(f"SYSTEM_ERROR must not carry a recommendation body: {present}")
            return self

        if self.error_detail is not None:
            raise ValueError("error_detail is only allowed for SYSTEM_ERROR")
        if self.rules_severity is not None:
            raise ValueError("rules_severity is only allowed for SYSTEM_ERROR")
        missing = [name for name in ("issue_class", "severity", "stakeholder_summary")
                   if getattr(self, name) is None]
        if missing:
            raise ValueError(f"{status.value} requires {missing}")

        if status == InvestigationStatus.INSUFFICIENT_EVIDENCE:
            if not self.insufficient_evidence_reason:
                raise ValueError("INSUFFICIENT_EVIDENCE requires insufficient_evidence_reason")
            if self.recommended_actions:
                raise ValueError("INSUFFICIENT_EVIDENCE must not carry recommended_actions")
        elif self.insufficient_evidence_reason is not None:
            raise ValueError("insufficient_evidence_reason is only allowed for INSUFFICIENT_EVIDENCE")
        return self

    def to_output_dict(self) -> dict[str, Any]:
        """Serialize for display, storage and logging.

        Null fields are dropped. For SYSTEM_ERROR every recommendation-body field
        is dropped as well, so a consumer cannot render a half-empty
        recommendation (Req. 8.1).
        """
        data = self.model_dump(mode="json", exclude_none=True)
        if self.status == InvestigationStatus.SYSTEM_ERROR:
            for name in BODY_FIELDS:
                data.pop(name, None)
        return data

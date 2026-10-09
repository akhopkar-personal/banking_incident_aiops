"""Incident State Store, review, resolution, feedback, preference and adaptation
schemas (Architecture Spec Sections 3.10 to 3.13; Req. Sections 12.5, 12.6)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field, model_validator

from .common import IncidentId, Rating, RunId, Schema, UtcDatetime
from .enums import (
    FixOutcome,
    IncidentState,
    IssueType,
    ReviewerRole,
    SeverityLevel,
    SignalType,
)

# ---------------------------------------------------------------- state store

StateEvent = Literal[
    "created", "ticket_upserted", "paged", "assigned", "reviewed",
    "reclassified", "resolved", "verified", "indexed",
    "acknowledged", "gate_handoff",  # v1.9: H-1, H-2
]


class IncidentStateRecord(Schema):
    record_id: str
    incident_id: IncidentId
    run_id: RunId
    event: StateEvent
    incident_state: IncidentState
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    at: UtcDatetime


# ------------------------------------------------------- review and resolution


class Ratings(Schema):
    rca: Rating
    actions: Rating
    severity: Rating
    summary: Rating

    @property
    def mean(self) -> float:
        return (self.rca + self.actions + self.severity + self.summary) / 4


class ReviewDecision(Schema):
    incident_id: IncidentId
    run_id: RunId
    decision: Literal["approve", "edit", "reject"]
    reason: Optional[str] = None
    issue_type: Optional[IssueType] = None
    ratings: Optional[Ratings] = None
    edited_output: Optional[dict[str, Any]] = None
    high_risk_confirmations: list[int] = Field(default_factory=list)
    reviewer_role: ReviewerRole
    at: UtcDatetime

    @model_validator(mode="after")
    def _edit_and_reject_need_details(self) -> "ReviewDecision":
        if self.decision in ("edit", "reject"):
            missing = [name for name in ("reason", "issue_type", "ratings") if not getattr(self, name)]
            if missing:
                raise ValueError(f"{self.decision} requires {missing} (Req. FR-19, FR-58)")
        if self.decision == "edit" and not self.edited_output:
            raise ValueError("edit requires edited_output")
        return self


class Reclassification(Schema):
    incident_id: IncidentId
    from_level: SeverityLevel
    to_level: SeverityLevel
    reason: str = Field(min_length=1)
    reviewer_role: ReviewerRole
    at: UtcDatetime

    @model_validator(mode="after")
    def _only_to_major(self) -> "Reclassification":
        if self.to_level not in (SeverityLevel.S1, SeverityLevel.S2):
            raise ValueError("re-classification can only raise an incident to S1 or S2 (Req. FR-53)")
        return self


class ResolutionRecord(Schema):
    incident_id: IncidentId
    actual_root_cause: str = Field(min_length=1)
    actions_taken: str
    fix_outcome: FixOutcome
    resolution_time_min: float = Field(ge=0.0)
    resolver_role: ReviewerRole
    at: UtcDatetime


class VerificationRecord(Schema):
    incident_id: IncidentId
    verdict: Literal["confirmed", "corrected", "rejected"]
    corrected_root_cause: Optional[str] = None
    verifier_role: Literal[ReviewerRole.SME, ReviewerRole.ESCALATION]
    at: UtcDatetime

    @model_validator(mode="after")
    def _corrected_needs_text(self) -> "VerificationRecord":
        if self.verdict == "corrected" and not self.corrected_root_cause:
            raise ValueError("a corrected verdict requires corrected_root_cause")
        return self


class FeedbackCandidate(Schema):
    candidate_id: str
    feedback_id: str
    incident_id: IncidentId
    run_id: RunId
    langfuse_trace_id: Optional[str] = None
    original_incident: dict[str, Any]
    original_output: dict[str, Any]
    reviewer_correction: str
    reviewer_reason: str
    issue_type: Optional[IssueType] = None
    prompt_version_set: dict[str, str] = Field(default_factory=dict)
    status: Literal["pending", "promoted", "discarded"] = "pending"
    created_at: UtcDatetime
    resolved_at: Optional[UtcDatetime] = None
    resolved_by: Optional[ReviewerRole] = None
    discard_reason: Optional[str] = None
    golden_dataset_version_added: Optional[int] = None

    @model_validator(mode="after")
    def _status_fields(self) -> "FeedbackCandidate":
        if self.status == "discarded" and not self.discard_reason:
            raise ValueError("a discarded candidate requires discard_reason")
        if self.status == "promoted" and self.golden_dataset_version_added is None:
            raise ValueError("a promoted candidate requires golden_dataset_version_added")
        return self


# ------------------------------------------------------- preferences and reward


class PreferenceIntegrity(Schema):
    agreement_status: Literal["single", "agreed", "disputed", "tie_broken"] = "single"
    duplicate_of: Optional[str] = None
    pii_checked: bool = False


class PreferenceRecord(Schema):
    feedback_id: str
    signal_type: SignalType
    incident_id: IncidentId
    run_id: RunId
    langfuse_trace_id: Optional[str] = None
    scenario_id: Optional[str] = None
    prompt_version_set: dict[str, str] = Field(default_factory=dict)
    agent: Optional[str] = None
    reviewer_role: ReviewerRole
    reviewer_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDatetime
    integrity: PreferenceIntegrity = Field(default_factory=PreferenceIntegrity)


class PairwisePayload(Schema):
    pair_id: str
    session_id: str
    candidate_a_run_id: RunId
    candidate_b_run_id: RunId
    version_a: str
    version_b: str
    shown_order: Literal["AB", "BA"]
    choice: Literal["A", "B", "tie"]
    reason: str
    held_out: bool


class GateHandoffPayload(Schema):
    found_in_ranked_list: bool
    rank_found: Optional[int] = Field(default=None, ge=1)


class RewardComponents(Schema):
    mean_rating: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    win_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    approval_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    fix_outcome_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RewardScore(Schema):
    prompt_version_set_key: str
    components: RewardComponents
    weights: dict[str, float]
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    signal_count: int = Field(ge=0)
    sufficient: bool
    computed_at: UtcDatetime


# ---------------------------------------------------- adaptation and rules


class MetricSnapshot(Schema):
    metric_name: str
    value: float


ALLOWED_CHANGE_TYPES = ("prompt_rule", "few_shot_example", "retrieval_alias")


class AdaptationLogEntry(Schema):
    adaptation_id: str
    status: Literal["proposed", "waiting", "approved", "applied", "rejected", "reverted"]
    source: Literal["feedback", "preference", "implicit"]
    trigger_pattern: dict[str, Any]
    proposed_change: dict[str, Any]
    feedback_ids: list[str] = Field(default_factory=list)
    before_metrics: list[MetricSnapshot] = Field(default_factory=list)
    after_metrics: list[MetricSnapshot] = Field(default_factory=list)
    regression_check_passed: Optional[bool] = None
    held_out_win_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    length_change_pct: Optional[float] = None
    explanation: Optional[str] = None
    approved_by: Optional[ReviewerRole] = None
    reject_reason: Optional[str] = None
    prompt_version_before: Optional[str] = None
    prompt_version_after: Optional[str] = None
    created_at: UtcDatetime
    decided_at: Optional[UtcDatetime] = None

    @model_validator(mode="after")
    def _scope_and_status(self) -> "AdaptationLogEntry":
        # Adaptation scope limit (Req. Section 14, Architecture invariant 6).
        change_type = self.proposed_change.get("type")
        if change_type not in ALLOWED_CHANGE_TYPES:
            raise ValueError(f"proposed_change.type must be one of {ALLOWED_CHANGE_TYPES}, got {change_type!r}")
        if self.status == "rejected" and not self.reject_reason:
            raise ValueError("a rejected adaptation requires reject_reason")
        if self.status in ("approved", "applied", "reverted") and self.approved_by is None:
            raise ValueError(f"status {self.status} requires approved_by")
        if self.approved_by is not None and self.approved_by not in (
            ReviewerRole.SME, ReviewerRole.EVALUATION_ENGINEER
        ):
            raise ValueError("only the SME or the Evaluation Engineer can approve an adaptation (Req. FR-32)")
        return self


class RulesChangeEntry(Schema):
    change_id: str
    rules_version_before: str
    rules_version_after: str
    rule_ids_changed: list[str] = Field(min_length=1)
    reason: str
    triggering_feedback_ids: list[str] = Field(default_factory=list)
    before_metrics: list[MetricSnapshot] = Field(default_factory=list)
    after_metrics: list[MetricSnapshot] = Field(default_factory=list)
    approved_by: Literal[ReviewerRole.SME] = ReviewerRole.SME
    at: UtcDatetime

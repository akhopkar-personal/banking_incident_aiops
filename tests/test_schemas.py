"""T-SCHEMA: data contracts (Architecture Spec Section 3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.schemas.analysis import (
    AffectedService,
    ChangeFinding,
    FiredRule,
    Hypothesis,
    ProposedAction,
    RcaResult,
    RecommendationResult,
    RulesResult,
    SummaryFields,
    TriageResult,
)
from src.schemas.enums import (
    InvestigationStatus,
    IncidentState,
    IssueClass,
    ReviewerRole,
    RiskLevel,
    SeverityGroup,
    SeverityLevel,
    most_severe,
)
from src.schemas.evidence import (
    ApiMetricRecord,
    EvidenceItem,
    RECORD_MODELS,
    ToolSummary,
    source_of,
)
from src.schemas.feedback import (
    AdaptationLogEntry,
    FeedbackCandidate,
    Ratings,
    Reclassification,
    ReviewDecision,
    VerificationRecord,
)
from src.schemas.incident import AnomalyEvent, IncidentObject
from src.schemas.output import (
    DispatchRecord,
    ErrorDetail,
    InvestigationOutput,
    RecommendedAction,
    SeverityAssessment,
)

# ---------------------------------------------------------------- helpers


def incident(t0, **overrides):
    data = dict(
        incident_id="INC-20260314-001", run_id="RUN-1", idempotency_key="abc123",
        input_mode="alert_json", raw_input="Payment API error rate > 15%",
        reference_time=t0, window_start=t0 - timedelta(minutes=60), window_end=t0 + timedelta(minutes=15),
    )
    data.update(overrides)
    return IncidentObject(**data)


def severity(level="S1"):
    return SeverityAssessment(level=level, rationale="customer error rate 38%")


def action(**overrides):
    data = dict(step=1, action="Roll back payments-service to v2.13.4", action_type="rollback_release",
                risk_level="medium", runbook_citation="RB-PAY-003 §2", expected_effect="errors drop")
    data.update(overrides)
    return RecommendedAction(**data)


def output(status, **overrides):
    data = dict(incident_id="INC-20260314-001", run_id="RUN-1", status=status, incident_state="PAGED")
    if status != InvestigationStatus.SYSTEM_ERROR:
        data.update(issue_class="release_regression", severity=severity(), stakeholder_summary="Transfers failing.")
    data.update(overrides)
    return InvestigationOutput(**data)


def error_detail():
    return ErrorDetail(failed_node="triage_agent", error_type="llm_unavailable", retry_count=2,
                       message="The investigation could not complete.")


# ------------------------------------------------------------------ enums


def test_severity_order_and_group():
    assert SeverityLevel.S1.is_more_severe_than(SeverityLevel.S2)
    assert not SeverityLevel.S3.is_more_severe_than(SeverityLevel.S2)
    assert SeverityLevel.S2.group == SeverityGroup.MAJOR
    assert SeverityLevel.S3.group == SeverityGroup.LOW_MEDIUM
    assert most_severe(SeverityLevel.S3, SeverityLevel.S1, SeverityLevel.S2) == SeverityLevel.S1
    assert most_severe() == SeverityLevel.S4


# --------------------------------------------------------------- incident


def test_incident_valid_and_utc(t0):
    inc = incident(t0, reference_time=datetime(2026, 3, 14, 15, 35, tzinfo=timezone(timedelta(hours=5, minutes=30))))
    assert inc.reference_time.tzinfo == timezone.utc
    assert inc.reference_time.hour == 10


@pytest.mark.parametrize("field,value", [
    ("incident_id", "INC-2026-1"),
    ("run_id", "run-1"),
    ("idempotency_key", ""),
])
def test_incident_rejects_bad_ids(t0, field, value):
    with pytest.raises(ValidationError):
        incident(t0, **{field: value})


def test_incident_rejects_naive_datetime(t0):
    with pytest.raises(ValidationError):
        incident(t0, reference_time=datetime(2026, 3, 14, 10, 5))


def test_incident_window_order(t0):
    with pytest.raises(ValidationError, match="window_end must be after window_start"):
        incident(t0, window_end=t0 - timedelta(minutes=61))


def test_incident_rejects_unknown_field(t0):
    with pytest.raises(ValidationError):
        incident(t0, sevrity="S1")


def test_anomaly_event(t0):
    event = AnomalyEvent(anomaly_id="ANM-1", service="payments-service", signal="customer_error_rate_pct",
                         observed=38.0, baseline=0.5, z_score=12.3, detected_at=t0,
                         evidence_ids=["API-0113"], method="zscore")
    assert event.method == "zscore"
    with pytest.raises(ValidationError):
        AnomalyEvent(anomaly_id="ANM-1", service="x", signal="s", observed=1, baseline=0,
                     detected_at=t0, evidence_ids=["BAD-1"], method="zscore")


# --------------------------------------------------------------- evidence


def test_every_source_has_a_record_model():
    from src.schemas.enums import TelemetrySource
    assert set(RECORD_MODELS) == set(TelemetrySource)
    assert source_of("KFK-0042") == TelemetrySource.KFK


def test_api_metric_bounds(t0):
    record = ApiMetricRecord(timestamp=t0, service="payments-service", record_id="API-1", endpoint="/transfer",
                             request_count=100, error_rate=0.38, p95_latency_ms=900)
    assert record.environment == "prod"
    with pytest.raises(ValidationError):
        ApiMetricRecord(timestamp=t0, service="s", record_id="API-2", endpoint="/x",
                        request_count=1, error_rate=38, p95_latency_ms=1)


def test_tool_summary_caps_notable_records(t0):
    item = EvidenceItem(evidence_id="LOG-1", source="LOG", service="s", timestamp=t0, summary="x")
    ToolSummary(tool="query_logs", window_start=t0, window_end=t0, record_count=20, notable=[item] * 20)
    with pytest.raises(ValidationError):
        ToolSummary(tool="query_logs", window_start=t0, window_end=t0, record_count=21, notable=[item] * 21)


# --------------------------------------------------------------- analysis


def test_triage_result_bounds():
    TriageResult(issue_class="release_regression", proposed_severity="S1",
                 affected_services=[AffectedService(service="payments-service", role="origin")],
                 confidence=0.8, evidence_ids=["DEP-0007"], rationale="release before onset")
    with pytest.raises(ValidationError):
        TriageResult(issue_class="release_regression", proposed_severity="S1", affected_services=[],
                     confidence=1.2, evidence_ids=[], rationale="")
    with pytest.raises(ValidationError):
        TriageResult(issue_class="unknown_class", proposed_severity="S1", affected_services=[],
                     confidence=0.5, evidence_ids=[], rationale="")


def test_rules_result_group_must_match_level(t0):
    RulesResult(pass_name="pass1", level="S2", group="major", rules_version="rules-v1", evaluated_at=t0,
                rules_fired=[FiredRule(rule_id="R-S2-URP", observed=6, threshold=0, severity="S2")])
    with pytest.raises(ValidationError, match="does not match"):
        RulesResult(pass_name="pass1", level="S3", group="major", rules_version="rules-v1", evaluated_at=t0)


def test_rca_ranks_must_be_sequential():
    h1 = Hypothesis(rank=1, root_cause="a", confidence=0.8, supporting_evidence=[], contradicting_or_missing_evidence=[])
    h3 = Hypothesis(rank=3, root_cause="b", confidence=0.2, supporting_evidence=[], contradicting_or_missing_evidence=[])
    RcaResult(hypotheses=[h1])
    with pytest.raises(ValidationError, match="ranks"):
        RcaResult(hypotheses=[h1, h3])


def test_change_finding_requires_change_record_id():
    ChangeFinding(change_id="DEP-0007", change_type="release", service="payments-service",
                  time_before_onset_min=10, on_dependency_path=True, linked_hypothesis_rank=1, rationale="r")


def test_recommendation_result_text_fields_are_required():
    with pytest.raises(ValidationError):
        RecommendationResult(root_cause_summary="x", top_confidence=0.8, recommended_actions=[],
                             summary_fields=SummaryFields(what_happened="a", customer_impact="b",
                                                          current_status="c", next_update="d"),
                             cited_doc_ids=[], cited_evidence_ids=[])  # regulatory_notes etc. missing
    RecommendationResult(root_cause_summary="x", top_confidence=0.8,
                         recommended_actions=[ProposedAction(step=1, action="a", action_type="restart_service",
                                                             risk_level="low", runbook_citation="RB-GEN-001 §1",
                                                             expected_effect="e")],
                         summary_fields=SummaryFields(what_happened="a", customer_impact="b",
                                                      current_status="c", next_update="d"),
                         regulatory_notes="", insufficient_evidence_reason="",
                         cited_doc_ids=["RB-GEN-001"], cited_evidence_ids=[])


# ----------------------------------------------------------------- output


def test_recommended_action_always_requires_approval():
    assert action().requires_approval is True
    with pytest.raises(ValidationError, match="requires_approval"):
        action(requires_approval=False)


def test_pending_review_output_valid_and_round_trips():
    out = output(InvestigationStatus.PENDING_REVIEW, recommended_actions=[action()],
                 dispatch=DispatchRecord(paged=True, fast_path=True))
    restored = InvestigationOutput.model_validate_json(out.model_dump_json())
    assert restored == out


def test_pending_review_requires_severity_and_summary():
    with pytest.raises(ValidationError, match="requires"):
        InvestigationOutput(incident_id="INC-20260314-001", run_id="RUN-1",
                            status="PENDING_REVIEW", incident_state="OPEN")


def test_system_error_valid_with_dispatch_and_rules(t0):
    rules = RulesResult(pass_name="pass1", level="S1", group="major", rules_version="rules-v1", evaluated_at=t0)
    out = output(InvestigationStatus.SYSTEM_ERROR, error_detail=error_detail(), rules_severity=rules,
                 dispatch=DispatchRecord(paged=True, fast_path=True, itsm_ticket_id="MOCK-ITSM-0007"))
    data = out.to_output_dict()
    for absent in ("severity", "hypotheses", "recommended_actions", "stakeholder_summary",
                   "timeline", "evidence", "needs_human_rca", "issue_class"):
        assert absent not in data, absent
    assert data["error_detail"]["error_type"] == "llm_unavailable"
    assert data["dispatch"]["paged"] is True
    assert data["rules_severity"]["level"] == "S1"


def test_system_error_requires_error_detail():
    with pytest.raises(ValidationError, match="requires error_detail"):
        output(InvestigationStatus.SYSTEM_ERROR)


@pytest.mark.parametrize("body", [
    {"severity": SeverityAssessment(level="S1", rationale="r")},
    {"stakeholder_summary": "summary"},
    {"recommended_actions": [RecommendedAction(step=1, action="a", action_type="t", risk_level="low",
                                               runbook_citation="RB-1", expected_effect="e")]},
    {"hypotheses": [Hypothesis(rank=1, root_cause="x", confidence=0.5, supporting_evidence=[],
                               contradicting_or_missing_evidence=[])]},
])
def test_system_error_forbids_recommendation_body(body):
    with pytest.raises(ValidationError, match="must not carry"):
        output(InvestigationStatus.SYSTEM_ERROR, error_detail=error_detail(), **body)


def test_error_detail_only_for_system_error():
    with pytest.raises(ValidationError, match="only allowed for SYSTEM_ERROR"):
        output(InvestigationStatus.PENDING_REVIEW, error_detail=error_detail())


def test_insufficient_evidence_rules():
    out = output(InvestigationStatus.INSUFFICIENT_EVIDENCE, insufficient_evidence_reason="no telemetry for window")
    assert out.to_output_dict()["insufficient_evidence_reason"]
    with pytest.raises(ValidationError, match="requires insufficient_evidence_reason"):
        output(InvestigationStatus.INSUFFICIENT_EVIDENCE)
    with pytest.raises(ValidationError, match="must not carry recommended_actions"):
        output(InvestigationStatus.INSUFFICIENT_EVIDENCE, insufficient_evidence_reason="x", recommended_actions=[action()])
    with pytest.raises(ValidationError, match="only allowed for INSUFFICIENT_EVIDENCE"):
        output(InvestigationStatus.PENDING_REVIEW, insufficient_evidence_reason="x")


# --------------------------------------------------------------- feedback


def test_review_decision_rules(t0):
    ReviewDecision(incident_id="INC-20260314-001", run_id="RUN-1", decision="approve",
                   reviewer_role="escalation", at=t0)
    with pytest.raises(ValidationError, match="reject requires"):
        ReviewDecision(incident_id="INC-20260314-001", run_id="RUN-1", decision="reject",
                       reason="wrong root cause", reviewer_role="escalation", at=t0)
    ReviewDecision(incident_id="INC-20260314-001", run_id="RUN-1", decision="reject", reason="wrong root cause",
                   issue_type="root_cause_error", ratings=Ratings(rca=1, actions=2, severity=4, summary=3),
                   reviewer_role="escalation", at=t0)
    with pytest.raises(ValidationError, match="edited_output"):
        ReviewDecision(incident_id="INC-20260314-001", run_id="RUN-1", decision="edit", reason="r",
                       issue_type="verbosity", ratings=Ratings(rca=3, actions=3, severity=3, summary=2),
                       reviewer_role="escalation", at=t0)


def test_ratings_range():
    assert Ratings(rca=1, actions=2, severity=4, summary=3).mean == 2.5
    with pytest.raises(ValidationError):
        Ratings(rca=0, actions=2, severity=4, summary=3)
    with pytest.raises(ValidationError):
        Ratings(rca=6, actions=2, severity=4, summary=3)


def test_reclassification_only_to_major(t0):
    Reclassification(incident_id="INC-20260314-001", from_level="S3", to_level="S2", reason="customers affected",
                     reviewer_role="production_support", at=t0)
    with pytest.raises(ValidationError, match="S1 or S2"):
        Reclassification(incident_id="INC-20260314-001", from_level="S4", to_level="S3", reason="r",
                         reviewer_role="production_support", at=t0)


def test_verification_rules(t0):
    with pytest.raises(ValidationError, match="corrected_root_cause"):
        VerificationRecord(incident_id="INC-20260314-001", verdict="corrected", verifier_role="sme", at=t0)
    with pytest.raises(ValidationError):
        VerificationRecord(incident_id="INC-20260314-001", verdict="confirmed",
                           verifier_role="production_support", at=t0)


def test_feedback_candidate_status_fields(t0):
    base = dict(candidate_id="C-1", feedback_id="FB-1", incident_id="INC-20260314-001", run_id="RUN-1",
                original_incident={}, original_output={}, reviewer_correction="c", reviewer_reason="r",
                created_at=t0)
    FeedbackCandidate(**base)
    with pytest.raises(ValidationError, match="discard_reason"):
        FeedbackCandidate(**base, status="discarded")
    with pytest.raises(ValidationError, match="golden_dataset_version_added"):
        FeedbackCandidate(**base, status="promoted")


def test_adaptation_scope_and_approver(t0):
    base = dict(adaptation_id="ADP-007", source="feedback", trigger_pattern={"issue_type": "root_cause_error"},
                created_at=t0)
    AdaptationLogEntry(**base, status="proposed", proposed_change={"type": "prompt_rule", "target": "rca_agent"})
    with pytest.raises(ValidationError, match="proposed_change.type"):
        AdaptationLogEntry(**base, status="proposed", proposed_change={"type": "model_change", "target": "gpt-4o"})
    with pytest.raises(ValidationError, match="requires approved_by"):
        AdaptationLogEntry(**base, status="applied", proposed_change={"type": "prompt_rule"})
    with pytest.raises(ValidationError, match="only the SME or the Evaluation Engineer"):
        AdaptationLogEntry(**base, status="applied", proposed_change={"type": "prompt_rule"},
                           approved_by=ReviewerRole.ESCALATION)

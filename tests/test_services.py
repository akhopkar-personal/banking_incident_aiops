"""Deterministic services (Architecture Spec Section 5). T-DETECT, T-RULES, T-DEDUP (grouping), T-ROUTE."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from data.generators import SCENARIOS
from src.schemas.analysis import AffectedService, RecommendationResult, SummaryFields, TriageResult
from src.schemas.enums import IncidentState, IssueClass, RunMode, ServiceRole, SeverityLevel
from src.schemas.incident import IncidentObject
from src.schemas.output import DispatchRecord, SeverityAssessment
from src.services import alert_correlator, anomaly_detector, gates, notification_service, severity_rules
from src.services import incident_state_store as store
from src.tools.dispatch_mocks import outbox
from src.tools.implementations._common import parse_time
from tests.data_helpers import Signals, manifest

ROOTS = {"SC-01": "payments-service", "SC-02": "kafka-platform", "SC-03": "core-banking-db",
         "SC-04": "payment-network-gateway", "SC-05": "payments-service"}


def window(dataset_id):
    m = manifest(dataset_id)
    return parse_time(m["window_start"]), parse_time(m["window_end"])


# ------------------------------------------------------------------ detector

@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_detector_finds_onset_and_nothing_in_baseline(sandbox, scenario_id):
    start, end = window(scenario_id)
    events = anomaly_detector.detect(scenario_id, start, end)
    minutes = [int((e.detected_at - start).total_seconds() // 60) for e in events]
    assert events and min(minutes) >= 20
    onset = SCENARIOS[scenario_id].onset_minute
    assert any(onset - 10 <= m <= onset + 1 for m in minutes)  # SC-05: complaints and duplicates from minute 20
    assert all(e.evidence_ids for e in events) and [e.anomaly_id for e in events] == [
        f"ANM-{i}" for i in range(1, len(events) + 1)]


def test_detector_respects_withheld_sources_and_survives_broken_files(sandbox):
    start, end = window("SC-02")
    signals = {e.signal for e in anomaly_detector.detect("SC-02", start, end, withheld_sources=["KFK"])}
    assert not signals & {"broker_down", "kafka_under_replicated", "consumer_lag"}
    start, end = window("FX-FAILURE")
    assert anomaly_detector.detect("FX-FAILURE", start, end)  # logs are unreadable; the rest still works


# ---------------------------------------------------------------- correlator

@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_one_incident_group_per_scenario_with_expected_root(sandbox, scenario_id):
    start, end = window(scenario_id)
    groups = alert_correlator.group(anomaly_detector.detect(scenario_id, start, end))
    assert len(groups) == 1 and groups[0].root_service == ROOTS[scenario_id]
    assert groups[0].idempotency_key == alert_correlator.idempotency_key(groups[0].root_service,
                                                                         groups[0].bucket_start)


def test_alert_storm_is_one_group_and_replay_has_the_same_key(sandbox):
    start, end = window("FX-ALERT-STORM")
    storm = alert_correlator.group(anomaly_detector.detect("FX-ALERT-STORM", start, end))
    assert len(storm) == 1 and storm[0].root_service == "kafka-platform"
    again = alert_correlator.group(anomaly_detector.detect("FX-ALERT-STORM", start, end))
    assert again[0].idempotency_key == storm[0].idempotency_key


def test_unconnected_services_in_different_buckets_are_separate(sandbox):
    start, end = window("SC-02")
    events = anomaly_detector.detect("SC-02", start, end)
    late = [e.model_copy(update={"detected_at": e.detected_at + timedelta(hours=3), "service": "core-banking-db",
                                 "signal": "db_cpu_pct"}) for e in events[:1]]
    assert len(alert_correlator.group(events + late)) == 2


def test_upsert_incident_dedups(sandbox):
    start, end = window("SC-01")
    group = alert_correlator.group(anomaly_detector.detect("SC-01", start, end))[0]
    first = alert_correlator.upsert_incident(group, reference_time=start, run_id="RUN-1", scenario_id="SC-01")
    second = alert_correlator.upsert_incident(group, reference_time=start, run_id="RUN-2", scenario_id="SC-01")
    assert first == ("INC-20260314-001", True) and second == ("INC-20260314-001", False)
    log = (sandbox.logs_dir / "interactions.log").read_text(encoding="utf-8")
    assert '"event": "incident_deduplicated"' in log and '"event": "incident_created"' in log


# -------------------------------------------------------------- severity rules

@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_rules_engine_matches_expected_and_the_data_oracle(sandbox, scenario_id):
    start, end = window(scenario_id)
    result = severity_rules.evaluate("pass1", severity_rules.compute_signals(scenario_id, start, end))
    fired = {r.rule_id for r in result.rules_fired}
    assert fired == Signals(scenario_id).fired_pass1()
    assert fired - {"R-S3-DEG"} == set(SCENARIOS[scenario_id].expected_rules_pass1)
    assert result.level.value == SCENARIOS[scenario_id].expected_severity and result.rules_version == "rules-v1"
    assert ("regulatory_flag" in result.flags) == (scenario_id == "SC-05")


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_missing_source_variant_severity(sandbox, scenario_id):
    s = SCENARIOS[scenario_id]
    start, end = window(scenario_id)
    signals = severity_rules.compute_signals(scenario_id, start, end, [s.withheld_source])
    assert severity_rules.evaluate("pass1", signals).level.value == s.expected_severity


def series(values, start_minute=0):
    t0 = parse_time("2026-03-14T09:35:00Z")
    return {"svc": {t0 + timedelta(minutes=start_minute + i): float(v) for i, v in enumerate(values)}}


@pytest.mark.parametrize("values,fires", [
    ([16, 16, 16], True),      # > 15 for 3 minutes
    ([16, 16, 15], False),     # 15 is not above 15
    ([16, 16, 10, 16], False),  # not consecutive
])
def test_sustained_threshold_boundaries(sandbox, values, fires):
    signals = {"customer_error_rate_pct": severity_rules.SignalValue(max(values), series(values))}
    fired = {r.rule_id for r in severity_rules.evaluate("pass1", signals).rules_fired}
    assert ("R-S1-ERR" in fired) == fires


def test_no_rule_fired_gives_s4(sandbox):
    result = severity_rules.evaluate("pass1", {})
    assert result.level == SeverityLevel.S4 and result.rules_fired == []


def triage(issue_class=IssueClass.DATA_INTEGRITY, proposed=SeverityLevel.S2, services=("payments-service",)):
    return TriageResult(issue_class=issue_class, proposed_severity=proposed,
                        affected_services=[AffectedService(service=s, role=ServiceRole.IMPACTED) for s in services],
                        confidence=0.7, evidence_ids=[], rationale="")


def test_pass2_raises_only(sandbox):
    pass1 = severity_rules.evaluate("pass1", {"complaints_per_product_30min": severity_rules.SignalValue(
        10, series([10]))})
    assert pass1.level == SeverityLevel.S4
    pass2 = severity_rules.evaluate("pass2", {}, triage(), pass1)
    assert pass2.level == SeverityLevel.S2 and "regulatory_flag" in pass2.flags
    assert {r.rule_id for r in pass2.rules_fired} == {"R-P2-INTEG"}
    channel = severity_rules.evaluate("pass2", {}, triage(IssueClass.OTHER, services=("mobile-app",)), pass1)
    assert channel.level == SeverityLevel.S3
    s1 = pass1.model_copy(update={"level": SeverityLevel.S1, "group": SeverityLevel.S1.group})
    assert severity_rules.evaluate("pass2", {}, triage(IssueClass.OTHER, services=()), s1).level == SeverityLevel.S1


def test_rescore_uses_confirmed_inputs_once(sandbox):
    start, end = window("SC-02")
    signals = severity_rules.compute_signals("SC-02", start, end)
    pass2 = severity_rules.evaluate("pass2", signals, triage(IssueClass.EVENTHUB_BROKER))
    assert pass2.level == SeverityLevel.S2
    assert severity_rules.would_raise(pass2, signals, {"offline_partitions": 3})  # confirmed: no 5-minute wait
    assert not severity_rules.would_raise(pass2, signals, {"consumer_lag_payments": 90_000})
    rescored = severity_rules.evaluate("rescore", severity_rules.apply_confirmed_inputs(
        signals, {"offline_partitions": 3}), None, pass2)
    assert rescored.level == SeverityLevel.S1 and "R-S1-OFFLINE" in {r.rule_id for r in rescored.rules_fired}


# --------------------------------------------------------------------- gates

def incident(scenario_id="SC-01", incident_id="INC-20260314-001", run_id="RUN-1", key="key-1"):
    start, end = window(scenario_id)
    return IncidentObject(incident_id=incident_id, run_id=run_id, idempotency_key=key, input_mode="detection_replay",
                          scenario_id=scenario_id, raw_input="", reference_time=start, window_start=start,
                          window_end=end)


def state_for(scenario_id, run_mode=RunMode.LIVE, **extra):
    inc = incident(scenario_id)
    store.append(inc.incident_id, inc.run_id, "created", IncidentState.OPEN, "test",
                 {"idempotency_key": inc.idempotency_key, "root_service": ROOTS.get(scenario_id)})
    start, end = window(scenario_id)
    pass1 = severity_rules.evaluate("pass1", severity_rules.compute_signals(scenario_id, start, end))
    return {"incident": inc, "run_mode": run_mode, "rules_pass1": pass1, "dispatch": DispatchRecord(), **extra}


def test_fast_page_only_for_s1_and_page_attaches_instead_of_repaging(sandbox):
    assert gates.fast_page(state_for("SC-02")) == {}
    state = state_for("SC-01")
    update = gates.fast_page(state)
    assert update["dispatch"].paged and update["dispatch"].fast_path and update["dispatch"].page_time
    state["dispatch"] = update["dispatch"]
    state.update(gates.final_severity(state))
    assert gates.page(state) == {}
    pages = outbox.read("pages")
    assert [p["mode"] for p in pages] == ["new", "update"]
    log = (sandbox.logs_dir / "interactions.log").read_text(encoding="utf-8")
    assert '"event": "fast_path_page"' in log


def test_page_rate_limit(sandbox):
    state = state_for("SC-01")
    gates.fast_page(state)
    second = gates.fast_page(state_for("SC-01"))  # a new run state, same incident, within 15 minutes
    assert second["dispatch"].suppressed and len(outbox.read("pages")) == 1


def test_evaluation_mode_dispatches_nothing(sandbox):
    state = state_for("SC-01", RunMode.EVALUATION)
    update = gates.fast_page(state)
    assert update["dispatch"].suppressed and "page" in update["dispatch"].intended
    update = gates.rules_only(state)
    assert "itsm_upsert" in update["dispatch"].intended
    assert outbox.read("pages") == [] and outbox.read("itsm") == []


def test_final_severity_flags_ai_disagreement(sandbox):
    state = state_for("SC-02", triage=triage(IssueClass.EVENTHUB_BROKER, SeverityLevel.S1))
    assessment = gates.final_severity(state)["final_severity"]
    assert assessment.level == SeverityLevel.S2 and assessment.llm_proposed_level == SeverityLevel.S1
    assert "ai_suggests_higher_severity" in assessment.flags and assessment.source == "rules"


def test_confidence_gate(sandbox):
    rec = RecommendationResult(root_cause_summary="x", top_confidence=0.49, recommended_actions=[],
                               summary_fields=SummaryFields(what_happened="", customer_impact="", current_status="",
                                                            next_update=""), regulatory_notes="",
                               insufficient_evidence_reason="", cited_doc_ids=[], cited_evidence_ids=[])
    assert gates.confidence_gate(rec) == {"needs_human_rca": True}
    assert gates.confidence_gate(rec.model_copy(update={"top_confidence": 0.5})) == {"needs_human_rca": False}


def test_low_medium_is_assigned_to_production_support(sandbox):
    state = state_for("SC-02")
    state["rules_pass1"] = severity_rules.evaluate("pass1", {})  # S4
    update = gates.rules_only(state)
    assert update["dispatch"].assigned_queue == "production-support" and update["dispatch"].itsm_ticket_id
    assert outbox.read("pages") == [] and [l["operation"] for l in outbox.read("itsm")] == ["upsert", "assign"]
    assert store.current("INC-20260314-001").state == IncidentState.ASSIGNED


# -------------------------------------------------------------- notifications

SUMMARY = SummaryFields(what_happened="Fund transfers are failing.", customer_impact="Transfers fail for some users.",
                        current_status="Rollback in progress.", next_update="10:45 UTC")


def assessment(level, flags=()):
    return SeverityAssessment(level=level, rationale="r", flags=list(flags), llm_proposed_level=SeverityLevel.S1)


def test_major_notifications_follow_the_audience_matrix_and_dedup(sandbox):
    sent, _ = notification_service.notify("INC-20260314-001", "RUN-1", assessment(SeverityLevel.S1), SUMMARY,
                                          True, "payments-service")
    assert [(n.channel, n.audience) for n in sent] == [("chat", "incident-channel"), ("chat", "team-channel"),
                                                       ("email", "stakeholders")]
    lines = outbox.read("notifications")
    assert lines[0]["destination"] == "#inc-inc-20260314-001" and lines[1]["destination"] == "#team-payments"
    assert lines[0]["text"].startswith("[SIMULATED]") and "Needs human RCA" in lines[0]["text"]
    assert len(lines[2]["to"]) == 3 and lines[2]["subject"].startswith("[SIMULATED]")
    again, _ = notification_service.notify("INC-20260314-001", "RUN-2", assessment(SeverityLevel.S1), SUMMARY,
                                           True, "payments-service")
    assert again == [] and len(outbox.read("notifications")) == 3


def test_low_medium_notifies_team_channel_only_and_storms_are_suppressed(sandbox):
    sent, _ = notification_service.notify("INC-20260314-001", "RUN-1", assessment(SeverityLevel.S3), SUMMARY,
                                          False, "accounts-service")
    assert [(n.channel, n.audience) for n in sent] == [("chat", "team-channel")]
    for i in range(2, 6):
        store.append(f"INC-20260314-00{i}", "RUN-1", "created", IncidentState.OPEN, "test",
                     {"idempotency_key": f"k{i}", "root_service": "accounts-service"})
    sent, _ = notification_service.notify("INC-20260314-009", "RUN-1", assessment(SeverityLevel.S4), SUMMARY,
                                          False, "accounts-service")
    assert sent == [] and '"reason": "storm"' in (sandbox.logs_dir / "interactions.log").read_text(encoding="utf-8")


def test_notifications_in_evaluation_mode_are_only_intended(sandbox):
    sent, intended = notification_service.notify("INC-20260314-001", "RUN-1", assessment(SeverityLevel.S2), SUMMARY,
                                                 False, "kafka-platform", RunMode.EVALUATION)
    assert sent == [] and len(intended["notify"]) == 3 and outbox.read("notifications") == []


# --------------------------------------------------------------- state store

def test_state_store_fold_ids_and_outputs(sandbox):
    store.append("INC-20260314-001", "RUN-1", "created", IncidentState.OPEN, "x", {"idempotency_key": "12345678"})
    store.append("INC-20260314-001", "RUN-1", "paged", IncidentState.PAGED, "x", {"mode": "new"})
    view = store.current("INC-20260314-001")
    assert view.state == IncidentState.PAGED and view.idempotency_key == "12345678"  # lookup keys stay plain
    assert store.find_by_key("12345678") == "INC-20260314-001"
    assert store.next_incident_id(parse_time("2026-03-14T10:00:00Z")) == "INC-20260314-002"
    assert store.recently_paged("INC-20260314-001", 15)
    a, b = store.new_run_id(), store.new_run_id()
    assert a != b and a.startswith("RUN-")
    lines = (sandbox.incident_state_dir / "incidents.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["record_id"] for l in lines] == ["ISR-000001", "ISR-000002"]

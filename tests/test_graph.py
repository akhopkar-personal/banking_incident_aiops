"""LangGraph workflow end to end with a scripted LLM (Architecture Spec Sections 4, 6).
T-FAST, T-FALLBACK, T-FAIL, T-ROUTE, T-DEDUP, T-INJECT (graph level)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from data.generators import SCENARIOS
from src.agent import core_agent, llm, prompts
from src.agent.core_agent import IntakeRejection, run_investigation
from src.schemas.enums import ErrorType, IncidentState, InvestigationStatus, ReviewerRole, RunMode, SeverityLevel
from src.schemas.feedback import Reclassification
from src.services import incident_state_store as store
from src.tools.dispatch_mocks import outbox
from tests.fake_llm import FakeChatModel, ScenarioOracle
from tests.data_helpers import manifest


@pytest.fixture
def fake(sandbox):
    """Install a scripted model for a scenario: fake(scenario_id, **oracle_options)."""
    models = []

    def install(scenario_id: str, **options) -> FakeChatModel:
        model_options = {k: options.pop(k) for k in ("fail_with", "delay_s", "finish_reason") if k in options}
        model = FakeChatModel(ScenarioOracle(scenario_id, **options), **model_options)
        llm.set_chat_model_factory(lambda: model)
        models.append(model)
        return model

    yield install
    llm.set_chat_model_factory(None)


def pages():
    return outbox.read("pages")


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_scenario_end_to_end(fake, scenario_id):
    s = SCENARIOS[scenario_id]
    model = fake(scenario_id)
    nodes: list[str] = []
    out = run_investigation(scenario_id=scenario_id, progress_callback=lambda n, u: nodes.append(n))
    assert out.status == InvestigationStatus.PENDING_REVIEW, out.metadata.guardrail_flags
    assert out.severity.level.value == s.expected_severity and out.severity.source == "rules"
    assert out.issue_class.value == s.issue_class and out.incident_state == IncidentState.PAGED
    assert out.hypotheses and out.recommended_actions and all(a.requires_approval for a in out.recommended_actions)
    assert all(a.action_type != "delete_topic" for a in out.recommended_actions)
    assert "action_denied" in out.metadata.guardrail_flags
    assert out.dispatch.paged and out.dispatch.itsm_ticket_id and len(out.dispatch.notifications) == 3
    assert [p["mode"] for p in pages()] == (["new", "update"] if s.expected_severity == "S1" else ["new"])
    assert out.dispatch.fast_path == (s.expected_severity == "S1")
    assert nodes[-1] == "finalize" and "rules_only_dispatch" not in nodes
    assert set(out.metadata.prompt_version_set) == set(prompts.AGENTS)
    assert out.metadata.token_counts and out.metadata.cost_estimate_usd > 0
    for hypothesis in out.hypotheses:  # grounding: every cited ID is in the output's evidence
        assert set(hypothesis.supporting_evidence) <= set(out.evidence)
    assert ("regulatory_flag" in out.severity.flags) == s.regulatory and bool(out.regulatory_notes) == s.regulatory
    saved = store.load_output(out.run_id)
    assert saved["incident_id"] == out.incident_id and saved["status"] == "PENDING_REVIEW"
    assert model.calls.count("tools") == 2  # one tool round plus the follow-up check


def test_fast_path_page_precedes_the_first_llm_answer(fake):
    """T-FAST: the S1 page is written before any LLM call has returned (FR-42)."""
    model = fake("SC-01", delay_s=0.3)
    out = run_investigation(scenario_id="SC-01")
    assert out.dispatch.fast_path and out.dispatch.page_time < min(model.finished_at)
    created = store.current(out.incident_id).created_at
    assert out.dispatch.page_time - created < timedelta(seconds=10)


def test_llm_down_fixture_ends_in_system_error_after_paging(fake):
    """T-FALLBACK (Req. 8.2 example): S1 is paged and ticketed; no recommendation body."""
    fake("SC-01")
    out = run_investigation(scenario_id="FX-LLM-DOWN")
    assert out.status == InvestigationStatus.SYSTEM_ERROR
    assert out.error_detail.failed_node == "triage_agent" and out.error_detail.error_type == ErrorType.LLM_UNAVAILABLE
    assert out.error_detail.retry_count == 2
    assert out.rules_severity.level == SeverityLevel.S1 and out.dispatch.paged and out.dispatch.fast_path
    assert out.dispatch.itsm_ticket_id
    body = out.to_output_dict()
    assert not {"severity", "hypotheses", "recommended_actions", "stakeholder_summary"} & set(body)
    assert [p["mode"] for p in pages()] == ["new"]


def test_kill_switch_runs_rules_only_without_any_llm_call(fake, sandbox):
    model = fake("SC-02")
    sandbox.llm_enabled = False
    out = run_investigation(scenario_id="SC-02")
    assert out.status == InvestigationStatus.SYSTEM_ERROR and out.error_detail.error_type == ErrorType.KILL_SWITCH
    assert out.dispatch.paged and out.rules_severity.level == SeverityLevel.S2 and model.calls == []
    assert "Monitoring rules" in out.error_detail.message or "rules-only" in out.error_detail.message


def test_failure_fixture_ends_in_system_error_within_the_retry_budget(fake, sandbox):
    """T-FAIL: query_logs fails permanently in the RCA Agent; the run still pages (S1)."""
    fake("SC-01")
    out = run_investigation(scenario_id="FX-FAILURE")
    assert out.status == InvestigationStatus.SYSTEM_ERROR and out.error_detail.failed_node == "rca_agent"
    assert out.error_detail.error_type == ErrorType.TOOL_CALL_FAILED and out.error_detail.retry_count == 2
    assert "Traceback" not in out.error_detail.message and "malformed" not in out.error_detail.message
    assert out.dispatch.paged
    errors = (sandbox.logs_dir / "error.log").read_text(encoding="utf-8")
    assert "malformed LOG record" in errors and '"stage": "node_failed"' in errors


def test_schema_invalid_output_is_retried_then_fails(fake):
    fake("SC-02", finish_reason="length")
    out = run_investigation(scenario_id="SC-02")
    assert out.status == InvestigationStatus.SYSTEM_ERROR and out.error_detail.error_type == ErrorType.SCHEMA_INVALID


def test_cost_cap_stops_the_run(fake, sandbox):
    fake("SC-02")
    sandbox.token_cap_per_run = 2000  # the fake reports 1,500 tokens per call
    out = run_investigation(scenario_id="SC-02")
    assert out.status == InvestigationStatus.SYSTEM_ERROR
    assert out.error_detail.error_type == ErrorType.COST_CAP_EXCEEDED and out.error_detail.retry_count == 0
    assert out.dispatch.paged


def test_low_confidence_flags_needs_human_rca(fake):
    fake("SC-02", confidence=0.4)
    out = run_investigation(scenario_id="SC-02")
    assert out.status == InvestigationStatus.PENDING_REVIEW and out.needs_human_rca
    notes = outbox.read("notifications")
    assert any("Needs human RCA" in n.get("text", "") for n in notes)


def test_very_low_confidence_is_insufficient_evidence(fake):
    model = fake("SC-02", confidence=0.2)
    out = run_investigation(scenario_id="SC-02")
    assert out.status == InvestigationStatus.INSUFFICIENT_EVIDENCE and out.recommended_actions == []
    assert out.insufficient_evidence_reason and out.dispatch.paged
    assert "RecommendationResult" not in model.calls  # no LLM call for the recommendation (FR-18)


def test_missing_source_variant_abstains_without_change_findings(fake):
    fake("SC-01", confidence=0.2)
    out = run_investigation(scenario_id="SC-01", withheld_sources=["DEP"])
    assert out.status == InvestigationStatus.INSUFFICIENT_EVIDENCE and out.change_findings == []
    assert "change records" in out.insufficient_evidence_reason


def test_ai_disagreement_is_flagged_but_rules_decide(fake):
    fake("SC-02", propose="S1")
    out = run_investigation(scenario_id="SC-02")
    assert out.severity.level == SeverityLevel.S2 and out.severity.llm_proposed_level == SeverityLevel.S1
    assert "ai_suggests_higher_severity" in out.severity.flags
    assert any("AI suggests higher severity" in n.get("text", "") for n in outbox.read("notifications"))


def test_rescore_raises_severity_once(fake):
    fake("SC-02", severity_inputs={"offline_partitions": 3})
    out = run_investigation(scenario_id="SC-02")
    assert out.severity.level == SeverityLevel.S1 and out.severity.rescored
    assert "R-S1-OFFLINE" in {r.rule_id for r in out.severity.rules_fired}


def test_evaluation_mode_dispatches_nothing(fake):
    fake("SC-01")
    out = run_investigation(scenario_id="SC-01", run_mode=RunMode.EVALUATION)
    assert out.status == InvestigationStatus.PENDING_REVIEW and out.metadata.run_mode == RunMode.EVALUATION
    assert out.dispatch.suppressed and {"page", "itsm_upsert", "notify"} <= set(out.dispatch.intended)
    assert pages() == [] and outbox.read("itsm") == [] and outbox.read("notifications") == []


def test_replay_reuses_the_incident_and_never_pages_twice(fake):
    fake("SC-03")
    first = run_investigation(scenario_id="SC-03")
    second = run_investigation(scenario_id="SC-03")
    assert second.incident_id == first.incident_id and second.run_id != first.run_id
    assert second.dispatch.itsm_ticket_id == first.dispatch.itsm_ticket_id
    assert [p["mode"] for p in pages()].count("new") == 1


def test_injection_fixture_is_flagged_and_does_not_change_the_outcome(fake, sandbox):
    fake("SC-03")
    out = run_investigation(scenario_id="FX-INJECTION")
    assert "injection_suspected" in out.metadata.guardrail_flags
    assert out.severity.level == SeverityLevel.S1 and out.dispatch.paged
    assert all(a.action_type != "delete_topic" for a in out.recommended_actions)
    assert "mode=cancel" not in json.dumps(out.to_output_dict())


def test_alert_json_and_free_text_inputs(fake):
    fake("SC-04")
    alert = json.dumps({"service": "payment-network-gateway", "timestamp": "2026-04-02T08:03:00Z",
                        "severity": "critical", "summary": "card route failures"})
    out = run_investigation(alert, scenario_id="SC-04")
    assert out.status == InvestigationStatus.PENDING_REVIEW and out.severity.level == SeverityLevel.S1
    fake("SC-05")
    out = run_investigation("Customers complaining about being charged twice since about 10:20 UTC",
                            scenario_id="SC-05")
    assert out.status == InvestigationStatus.PENDING_REVIEW and out.issue_class.value == "data_integrity"


@pytest.mark.parametrize("text,scenario,reason", [
    ("Customers are failing to log in since 10:05", None, "no_data_source"),
    ("What is the weather like today?", "SC-01", "not_an_incident_report"),
    ('{"service": "payments-service"}', "SC-01", "alert_json_missing_required_fields"),
    ("{not json", "SC-01", "alert_json_missing_required_fields"),
    ("Transfers are failing for customers", "SC-01", "no_time_window"),
])
def test_intake_rejections(fake, text, scenario, reason):
    fake("SC-01")
    out = run_investigation(text, scenario_id=scenario)
    assert isinstance(out, IntakeRejection) and out.reason == reason
    assert store.records() == []


def test_free_text_without_timezone_is_flagged(fake):
    fake("SC-01")
    out = run_investigation("Fund transfers failing since 10:05", scenario_id="SC-01")
    assert out.metadata.timezone_assumed_utc and "timezone_assumed_utc" in out.metadata.guardrail_flags


def test_reclassification_pages_through_the_final_gate(fake):
    fake("SC-02")
    out = run_investigation(scenario_id="SC-02")
    record = Reclassification(incident_id=out.incident_id, from_level=SeverityLevel.S2, to_level=SeverityLevel.S1,
                              reason="Customer balances wrong for many users",
                              reviewer_role=ReviewerRole.PRODUCTION_SUPPORT,
                              at=store.now())
    dispatch = core_agent.run_reclassification(out.incident_id, record)
    assert dispatch.paged and [p["mode"] for p in pages()] == ["new", "update"]
    assert {n.audience for n in dispatch.notifications} == {"incident-channel", "team-channel", "stakeholders"}


def test_mcp_transport_end_to_end(fake, sandbox):
    """The agents' tools over the real MCP server give the same outcome."""
    fake("SC-04")
    sandbox.tool_transport = "mcp"
    out = run_investigation(scenario_id="SC-04")
    assert out.status == InvestigationStatus.PENDING_REVIEW
    log = (sandbox.logs_dir / "interactions.log").read_text(encoding="utf-8")
    assert '"transport": "mcp"' in log and '"event": "mcp_server_started"' in log


def test_node_failure_logs_and_state_are_consistent(fake, sandbox):
    fake("SC-02", fail_with=RuntimeError("boom"))
    out = run_investigation(scenario_id="SC-02")
    assert out.error_detail.error_type == ErrorType.LLM_CALL_FAILED and "boom" not in out.error_detail.message
    assert manifest("SC-02")["window_start"]  # data untouched


@pytest.mark.llm
def test_real_llm_end_to_end(tmp_path, monkeypatch):
    """SC-04 through the real model, embeddings and index (python scripts/build_index.py). About $0.005."""
    from src.config import Settings, get_settings
    from src.logger_setup import configure_logging

    settings = Settings(outbox_dir=tmp_path / "outbox", incident_state_dir=tmp_path / "state",
                        logs_dir=tmp_path / "logs", tool_transport="inprocess")
    if settings.openai_api_key is None or not (settings.faiss_index_dir / "index.faiss").exists():
        pytest.skip("needs OPENAI_API_KEY and the knowledge-base index")
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    configure_logging(settings.logs_dir)
    llm.set_chat_model_factory(None)
    out = run_investigation(scenario_id="SC-04")
    assert out.status == InvestigationStatus.PENDING_REVIEW
    assert out.severity.level == SeverityLevel.S1 and out.issue_class.value == "network_tls"
    assert any(a.action_type == "renew_certificate" for a in out.recommended_actions)
    assert out.metadata.cost_estimate_usd < 0.05 and out.dispatch.fast_path
    get_settings.cache_clear()

"""Read tools, dispatch mocks and the tool registry (Architecture Spec Sections 7.1 to 7.4).
T-TOOLS, T-ACCESS."""

from __future__ import annotations

import json

import pytest

from src.errors import RecoverableError, ToolAccessDenied
from src.tools import implementations as impl
from src.tools.dispatch_mocks import chat_mock, email_mock, itsm_mock, outbox, pager_mock
from src.tools.implementations import ToolContext
from src.tools.implementations._common import parse_time
from src.tools.tool_registry import AGENTS, CONTEXT_KEYS, ToolRegistry
from tests.data_helpers import manifest


def ctx_for(scenario_id: str, **kw) -> tuple[ToolContext, str, str]:
    m = manifest(scenario_id)
    ctx = ToolContext(scenario_id=scenario_id, incident_id="INC-20260314-001", run_id="RUN-1",
                      window_start=parse_time(m["window_start"]), window_end=parse_time(m["window_end"]), **kw)
    return ctx, m["window_start"], m["window_end"]


# --------------------------------------------------------------- read tools

def test_query_logs_sc01(sandbox):
    ctx, start, end = ctx_for("SC-01")
    s = impl.query_logs(ctx, "payments-service", start, end, level="ERROR")
    assert s.aggregates["error_code.DB_TIMEOUT"] == 600 and s.record_count == 600
    assert "DB_TIMEOUT" in s.notable[0].summary and len(s.notable) <= 20
    assert manifest("SC-01")["key_evidence"]["first_db_timeout_log"] == s.notable[0].evidence_id


def test_query_kafka_events_sc02_and_sc05(sandbox):
    ctx, start, end = ctx_for("SC-02")
    s = impl.query_kafka_events(ctx, None, start, end)
    assert s.aggregates["max_lag"] == 85_000 and s.aggregates["broker_down_events"] == 1
    assert s.aggregates["max_under_replicated"] == 6 and s.aggregates["under_replicated_minutes"] == 25
    assert manifest("SC-02")["key_evidence"]["broker_down_event"] in {n.evidence_id for n in s.notable}
    ctx, start, end = ctx_for("SC-05")
    s = impl.query_kafka_events(ctx, None, start, end, topic="payments.transactions")
    assert s.aggregates["duplicate_transaction_ids"] == 140


def test_query_api_metrics_sc04(sandbox):
    ctx, start, end = ctx_for("SC-04")
    s = impl.query_api_metrics(ctx, ["payment-network-gateway", "payments-service"], start, end)
    assert s.aggregates["payment-network-gateway/routes/card.max_error_rate"] == 1.0
    assert s.aggregates["payment-network-gateway/routes/upi.max_error_rate"] < 0.01
    assert s.aggregates["payments-service/payments/v1/payments.max_error_rate"] >= 0.30
    assert s.notable[0].service == "payment-network-gateway"


def test_query_db_and_network(sandbox):
    ctx, start, end = ctx_for("SC-03")
    s = impl.query_db_infra_metrics(ctx, None, start, end)
    assert s.aggregates["max_connections_used_pct"] >= 97 and s.aggregates["max_cpu_pct"] > 90
    assert s.notable[0].summary.startswith("cbdb-prod-01 connections first above 90%")
    assert s.aggregates["max_lock_wait_ms"] >= 10 * s.aggregates["baseline_lock_wait_ms"] * 0.9
    ctx, start, end = ctx_for("SC-04")
    s = impl.query_network(ctx, None, start, end)
    assert s.aggregates["card.handshake_failures"] == 60 and s.aggregates["upi.handshake_failures"] == 0
    assert s.notable[0].evidence_id == manifest("SC-04")["key_evidence"]["tls_expired_event"]


def test_query_change_records_uses_lookback(sandbox):
    ctx, start, end = ctx_for("SC-05")
    ids = {n.evidence_id for n in impl.query_change_records(ctx, None, start, end).notable}
    assert {"DEP-0041", "DEP-0043"} <= ids  # DEP-0041 is 90 minutes before the window starts
    assert "DEP-0041" not in {n.evidence_id for n in impl.query_change_records(ctx, None, start, end,
                                                                               lookback_min=60).notable}


def test_query_complaints_clusters_and_redacts(sandbox):
    ctx, start, end = ctx_for("SC-05")
    s = impl.query_complaints(ctx, None, start, end)
    top = s.complaint_analysis.clusters[0]
    assert (top.product, top.topic) == ("card_payment", "double_charge") and top.volume >= 65
    assert top.sentiment_score < 0
    assert s.complaint_analysis.earliest_signal_time < parse_time(manifest("SC-05")["onset"])
    ctx, start, end = ctx_for("SC-01")
    text = json.dumps([n.model_dump(mode="json") for n in impl.query_complaints(ctx, None, start, end).notable])
    assert "07700 900" not in text


def test_eventhub_tools(sandbox):
    ctx, start, end = ctx_for("SC-02")
    q = impl.query_cluster_quorum(ctx, None, start, end)
    assert q.aggregates["max_offline_partitions"] == 0 and q.aggregates["session_expiration_samples"] == 25
    srg = impl.query_schema_registry(ctx, None, start, end)
    assert srg.aggregates["errors.marketing.offers-value"] == 1
    ctx, start, end = ctx_for("FX-INJECTION")
    con = impl.query_connect_status(ctx, None, start, end)
    assert con.aggregates["failed_tasks"] == 1 and con.notable[0].evidence_id == \
        manifest("FX-INJECTION")["key_evidence"]["injection_connect"]
    ctx, start, end = ctx_for("SC-01")
    acl = impl.query_acl_audit(ctx, None, start, end)
    assert acl.aggregates["denied"] == 0 and acl.record_count > 0


def test_withheld_source_and_failure_fixture(sandbox):
    ctx, start, end = ctx_for("SC-01", withheld_sources=("DEP",))
    s = impl.query_change_records(ctx, None, start, end)
    assert s.source_unavailable and s.record_count == 0 and not s.notable
    ctx, start, end = ctx_for("FX-FAILURE")
    with pytest.raises(RecoverableError, match="malformed LOG record"):
        impl.query_logs(ctx, None, start, end)


def test_dependencies_and_stakeholders(sandbox):
    info = impl.get_service_dependencies("core-banking-db")
    assert {"accounts-service", "payments-service", "mobile-app"} <= set(info.blast_radius_customer_facing)
    assert impl.get_service_dependencies("mobile-app", "upstream").upstream[:1] == ["api-gateway"]
    people = impl.lookup_stakeholders("payments-service", "major")
    assert len(people) == 3 and all(p["email"].endswith("@bank.example") for p in people)


def test_search_knowledge_base_hash_index(sandbox):
    results = impl.search_knowledge_base("TLS certificate expired on card route renewal", top_k=5,
                                         doc_types=["runbook"])
    assert results and all(r.doc_type == "runbook" for r in results)
    assert "RB-NET-004" in {r.doc_id for r in results}


# ------------------------------------------------------------ dispatch mocks

def test_dispatch_mocks_write_simulated_redacted_lines(sandbox):
    pager_mock.page_oncall("INC-20260314-001", "incident-escalation", "call 07700 900123", "new")
    chat_mock.send_chat_alert("#inc-x", "contact ops@bank.example", incident_id="INC-20260314-001")
    email_mock.send_email(["lead@bank.example"], "S1", "body", incident_id="INC-20260314-001")
    pages, notes = outbox.read("pages"), outbox.read("notifications")
    assert pages[0]["simulated"] is True and "900123" not in pages[0]["summary"]
    assert pages[0]["summary"].startswith("[SIMULATED]")
    assert "ops@bank.example" not in notes[0]["text"] and notes[1]["to"] == ["lead@bank.example"]
    with pytest.raises(ValueError):
        pager_mock.page_oncall("INC-20260314-001", "t", "s", "cancel")  # type: ignore[arg-type]


def test_itsm_upsert_is_idempotent_and_reset_archives(sandbox):
    first = itsm_mock.itsm_upsert_incident("abc", {"incident_id": "INC-20260314-001"})
    again = itsm_mock.itsm_upsert_incident("abc", {"incident_id": "INC-20260314-001"})
    other = itsm_mock.itsm_upsert_incident("def", {"incident_id": "INC-20260314-002"})
    assert first["ticket_id"] == again["ticket_id"] == "MOCK-ITSM-0001" and first["created"] and not again["created"]
    assert other["ticket_id"] == "MOCK-ITSM-0002"
    archive = outbox.reset_outbox()
    assert archive is not None and (archive / "itsm.jsonl").exists() and outbox.read("itsm") == []


# ------------------------------------------------------------------ registry

def test_agents_cannot_call_dispatch_tools(sandbox):
    registry = ToolRegistry()
    ctx, _, _ = ctx_for("SC-01")
    for agent in AGENTS:
        with pytest.raises(ToolAccessDenied):
            registry.call("page_oncall", {"incident_id": "x", "team": "t", "summary": "s"}, agent, ctx)
    content, flags, _ = registry.call_for_llm("send_email", {"to": [], "subject": "", "body": ""}, "rca_agent", ctx)
    assert content.startswith("Error:") and flags == ["tool_access_blocked"]
    errors = (sandbox.logs_dir / "error.log").read_text(encoding="utf-8")
    assert errors.count('"event": "tool_access_blocked"') == len(AGENTS) + 1
    assert outbox.read("pages") == [] and outbox.read("notifications") == []


@pytest.mark.parametrize("tool,caller,allowed", [
    ("query_logs", "rca_agent", True), ("query_logs", "triage_agent", False),
    ("query_change_records", "change_correlation_agent", True), ("query_change_records", "rca_agent", False),
    ("search_knowledge_base", "recommendation_agent", True), ("query_complaints", "triage_agent", True),
    ("lookup_stakeholders", "notify", True), ("lookup_stakeholders", "rca_agent", False),
    ("lookup_stakeholders", "finalize", False), ("query_logs", "rules_only_dispatch", True),
    ("page_oncall", "dispatch_fast_page", True), ("page_oncall", "notify", False),
    ("send_chat_alert", "notify", True), ("itsm_assign", "dispatch_page", False), ("no_such_tool", "rca_agent", False),
])
def test_access_policy(sandbox, tool, caller, allowed):
    registry = ToolRegistry()
    if allowed:
        assert registry.get(tool, caller).name == tool
    else:
        with pytest.raises(ToolAccessDenied):
            registry.get(tool, caller)


def test_bindings_hide_the_run_context(sandbox):
    registry = ToolRegistry()
    tools = {t.name: t for t in registry.as_langchain_tools("rca_agent")}
    assert "page_oncall" not in tools and "query_change_records" not in tools and "query_logs" in tools
    for tool in tools.values():
        assert not set(tool.args) & set(CONTEXT_KEYS), tool.name
    assert {t.name for t in registry.as_langchain_tools("change_correlation_agent")} == {
        "query_change_records", "get_service_dependencies"}


def test_registry_injects_context_and_default_window(sandbox):
    registry = ToolRegistry()
    ctx, _, _ = ctx_for("SC-01")
    # A model-supplied scenario_id is ignored: the run context decides which data is read.
    result = registry.call("query_logs", {"scenario_id": "SC-02", "level": "ERROR"}, "rca_agent", ctx)
    assert result.aggregates.get("error_code.DB_TIMEOUT") == 600
    content, flags, result = registry.call_for_llm("query_logs", {"service": ["payments-service"]}, "rca_agent", ctx)
    assert json.loads(content)["tool"] == "query_logs" and flags == []
    lines = (sandbox.logs_dir / "interactions.log").read_text(encoding="utf-8").splitlines()
    calls = [json.loads(line) for line in lines if '"tool_call"' in line]
    assert calls and all(c["transport"] == "inprocess" and c["incident_id"] == "INC-20260314-001" for c in calls)


def test_registry_wraps_bad_arguments_as_recoverable(sandbox):
    with pytest.raises(RecoverableError):
        ToolRegistry().call("get_service_dependencies", {"service": "no-such-service"}, "rca_agent")

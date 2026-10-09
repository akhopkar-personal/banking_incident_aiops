"""MCP server and transport switch (Architecture Spec Section 7.5; Req. FR-72, FR-73). T-MCP.

These tests start the real `incident-tools` server as a subprocess (a few seconds)."""

from __future__ import annotations

import json
import sys

import pytest

from src.errors import RecoverableError
from src.mcp_server.server import MCP_EXPOSED_TOOLS
from src.tools.mcp_bridge import McpBridge
from src.tools.tool_registry import ToolRegistry
from tests.test_tools import ctx_for

CALLS = [
    ("query_logs", {"service": ["payments-service"], "level": "ERROR"}, "SC-01"),
    ("query_kafka_events", {}, "SC-02"),
    ("query_api_metrics", {"service": ["payment-network-gateway"]}, "SC-04"),
    ("query_db_infra_metrics", {}, "SC-03"),
    ("query_network", {}, "SC-04"),
    ("query_change_records", {"lookback_min": 180}, "SC-05"),
    ("query_complaints", {}, "SC-01"),
    ("query_connect_status", {}, "FX-INJECTION"),
    ("query_acl_audit", {}, "SC-01"),
    ("query_schema_registry", {}, "SC-02"),
    ("query_cluster_quorum", {}, "SC-02"),
    ("get_service_dependencies", {"service": "kafka-platform"}, "SC-02"),
    ("search_knowledge_base", {"query": "consumer lag rebalance storm", "top_k": 3}, "SC-02"),
]


def dump(result):
    if isinstance(result, list):
        return [r.model_dump(mode="json") for r in result]
    return result.model_dump(mode="json")


def test_mcp_server_serves_exactly_the_read_tools_with_identical_results(sandbox):
    sandbox.tool_transport = "mcp"
    registry = ToolRegistry()
    try:
        assert {name for name, _, _ in CALLS} == set(MCP_EXPOSED_TOOLS)
        for name, args, scenario_id in CALLS:
            ctx, _, _ = ctx_for(scenario_id)
            caller = "change_correlation_agent" if name == "query_change_records" else "rca_agent"
            over_mcp = registry.call(name, args, caller, ctx)
            assert registry._bridge is not None and registry.transport == "mcp"
            sandbox.tool_transport = "inprocess"
            in_process = registry.call(name, args, caller, ctx)
            sandbox.tool_transport = "mcp"
            assert dump(over_mcp) == dump(in_process), name
        listed = set(registry._bridge.tool_names)
        assert listed == set(MCP_EXPOSED_TOOLS)
        assert not listed & {"page_oncall", "itsm_upsert_incident", "itsm_assign", "send_chat_alert", "send_email",
                             "lookup_stakeholders"}
        # A dispatch tool name cannot be called over MCP at all.
        with pytest.raises(RecoverableError):
            registry._bridge.call("page_oncall", {"incident_id": "x", "team": "t", "summary": "s"})
        # Tool failures cross the MCP boundary as RecoverableError (retried, then SYSTEM_ERROR).
        ctx, _, _ = ctx_for("FX-FAILURE")
        with pytest.raises(RecoverableError, match="malformed LOG record"):
            registry.call("query_logs", {}, "rca_agent", ctx)
        log = (sandbox.logs_dir / "interactions.log").read_text(encoding="utf-8")
        assert '"event": "mcp_server_started"' in log and '"transport": "mcp"' in log
    finally:
        registry.close()


def test_mcp_start_failure_falls_back_to_inprocess(sandbox, monkeypatch):
    sandbox.tool_transport = "mcp"
    sandbox.mcp_startup_timeout_s = 20
    original = McpBridge.__init__

    def broken(self, command=None):  # a server command that exits at once
        original(self, [sys.executable, "-c", "raise SystemExit(3)"])

    monkeypatch.setattr(McpBridge, "__init__", broken)
    registry = ToolRegistry()
    ctx, _, _ = ctx_for("SC-01")
    result = registry.call("query_logs", {"level": "ERROR"}, "rca_agent", ctx)
    assert result.record_count > 0 and registry.fallback_active and registry.transport == "inprocess"
    errors = [json.loads(line) for line in (sandbox.logs_dir / "error.log").read_text(encoding="utf-8").splitlines()]
    assert any(e["event"] == "mcp_server_error" for e in errors)

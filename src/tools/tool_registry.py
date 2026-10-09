"""Tool registry and access policy (Architecture Spec Section 7.1; Req. FR-66, FR-73).

- `call(name, args, caller, ctx)` checks the access policy, runs the tool over
  the configured transport, logs `tool_call`, and returns the typed result.
- Read tools served by the MCP server run over MCP when TOOL_TRANSPORT=mcp;
  everything else (dispatch mocks, lookup_stakeholders) always runs in-process.
- If the MCP server cannot start, the registry logs `mcp_server_error` and uses
  in-process calls for the rest of the process (`fallback_active`).
- `as_langchain_tools(agent)` gives an agent the schemas of the tools it may
  use, for `bind_tools`. The schemas leave out the run context (scenario,
  incident, run, withheld sources), which the registry adds to every call, so
  the model can never point a tool at other data.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field, create_model

from src import config
from src.errors import InvalidToolArguments, RecoverableError, ToolAccessDenied
from src.logger_setup import log_error, log_interaction
from src.mcp_server.server import MCP_EXPOSED_TOOLS
from src.schemas.analysis import RetrievedDocument
from src.schemas.evidence import DependencyInfo, ToolSummary
from src.tools import implementations as impl
from src.tools.dispatch_mocks import chat_mock, email_mock, itsm_mock, pager_mock
from src.tools.implementations import ToolContext

AGENTS = frozenset({"triage_agent", "rca_agent", "change_correlation_agent", "recommendation_agent"})
# Deterministic workflow nodes (Section 4.2) that may call read tools.
WORKFLOW_NODES = frozenset({
    "intake", "correlate_dedup", "redact", "severity_rules_pass1", "dispatch_fast_page", "itsm_upsert",
    "severity_rules_pass2", "analysis_join", "severity_rescore", "output_guardrails", "confidence_gate",
    "final_severity_gate", "dispatch_page", "dispatch_itsm_assign", "notify", "finalize", "rules_only_dispatch",
    "system_error",
})
DISPATCH_CALLERS = frozenset({"dispatch_fast_page", "itsm_upsert", "dispatch_page", "dispatch_itsm_assign", "notify",
                              "rules_only_dispatch"})
CONTEXT_KEYS = ("scenario_id", "incident_id", "run_id", "withheld_sources")


@dataclass
class ToolSpec:
    name: str
    kind: Literal["read", "dispatch"]
    allowed_callers: frozenset[str]
    description: str
    run: Callable[[dict[str, Any], ToolContext], Any]  # in-process call
    parse: Callable[[Any], Any] = lambda payload: payload  # MCP JSON -> typed result
    binding: Optional[type[BaseModel]] = None  # LLM-facing arguments, without context
    telemetry: bool = False  # takes the run context and a time window
    mcp: bool = False

    def __post_init__(self) -> None:
        self.mcp = self.name in MCP_EXPOSED_TOOLS


# ------------------------------------------------------------- tool table

_SERVICE = (Optional[list[str]], Field(default=None, description="Services to include; omit for all services"))
_WINDOW_START = (Optional[str], Field(default=None, description="ISO 8601 UTC start; omit for the incident window"))
_WINDOW_END = (Optional[str], Field(default=None, description="ISO 8601 UTC end; omit for the incident window"))


def _telemetry(name: str, fn: Callable[..., ToolSummary], allowed: set[str], description: str,
               **extra: tuple[Any, Any]) -> ToolSpec:
    binding = create_model(f"{name}_args", service=_SERVICE, window_start=_WINDOW_START, window_end=_WINDOW_END,
                           **extra)

    def run(args: dict[str, Any], ctx: ToolContext) -> ToolSummary:
        options = {k: v for k, v in args.items() if k not in ("service", "window_start", "window_end")}
        return fn(ctx, args.get("service"), args["window_start"], args["window_end"], **options)

    return ToolSpec(name, "read", frozenset(allowed), description, run, ToolSummary.model_validate, binding,
                    telemetry=True)


def _optional(description: str, kind: Any = str) -> tuple[Any, Any]:
    return (Optional[kind], Field(default=None, description=description))


RCA = {"rca_agent"}
SPECS: list[ToolSpec] = [
    ToolSpec(
        "search_knowledge_base", "read", frozenset({"triage_agent", "rca_agent", "recommendation_agent"}),
        "Search runbooks, postmortems, verified resolutions, service docs and regulatory notes.",
        lambda a, ctx: impl.search_knowledge_base(a["query"], a.get("top_k", 5), a.get("doc_types"),
                                                  a.get("include_unverified", False)),
        lambda p: [RetrievedDocument.model_validate(d) for d in p],
        create_model("search_knowledge_base_args",
                     query=(str, Field(description="What to look for, for example symptoms or a component")),
                     top_k=(int, Field(default=5, ge=1, le=10)),
                     doc_types=(Optional[list[Literal["runbook", "postmortem", "verified_resolution", "service_doc",
                                                      "regulatory"]]], Field(default=None)),
                     include_unverified=(bool, Field(default=False)))),
    _telemetry("query_logs", impl.query_logs, RCA, "Application logs: counts by level and error code, top messages.",
               level=_optional("ERROR, WARN or INFO"), error_code=_optional("Error code, for example DB_TIMEOUT")),
    _telemetry("query_kafka_events", impl.query_kafka_events, RCA,
               "Kafka: consumer lag, rebalances, broker failures, under-replicated partitions, duplicate transactions.",
               topic=_optional("Topic, for example payments.transactions"),
               event_type=_optional("produced, consumed, rebalance, broker_down or metric_sample")),
    _telemetry("query_api_metrics", impl.query_api_metrics, RCA,
               "API metrics per service and endpoint: max error rate, max p95, baseline p95.",
               endpoint=_optional("Endpoint path")),
    _telemetry("query_db_infra_metrics", impl.query_db_infra_metrics, RCA,
               "Database metrics: connection use, CPU, slow queries, lock waits.",
               db_instance=_optional("Database instance")),
    _telemetry("query_network", impl.query_network, RCA,
               "Network: TLS handshake failures by route, latency, certificate expiry.",
               destination=_optional("Destination, for example external-switch")),
    _telemetry("query_change_records", impl.query_change_records, {"change_correlation_agent"},
               "Change records from lookback_min minutes before the window start to its end.",
               lookback_min=(int, Field(default=180, ge=0, le=1440)),
               change_type=_optional("release, config, infra, acl, schema or connector_config")),
    _telemetry("query_complaints", impl.query_complaints, {"triage_agent", "rca_agent"},
               "Customer complaints: clusters, volume, sentiment, impact, earliest signal.",
               product=_optional("Product, for example fund_transfer")),
    _telemetry("query_connect_status", impl.query_connect_status, RCA, "Kafka Connect task states.",
               connector=_optional("Connector name")),
    _telemetry("query_acl_audit", impl.query_acl_audit, RCA, "Kafka ACL audit: denied operations.",
               resource=_optional("Resource, for example Topic:payments.transactions")),
    _telemetry("query_schema_registry", impl.query_schema_registry, RCA, "Schema Registry compatibility errors.",
               subject=_optional("Schema subject")),
    _telemetry("query_cluster_quorum", impl.query_cluster_quorum, RCA,
               "Kafka cluster quorum health, session expirations, offline partitions."),
    ToolSpec(
        "get_service_dependencies", "read", frozenset({"triage_agent", "rca_agent", "change_correlation_agent"}),
        "Service topology and customer-facing blast radius.",
        lambda a, ctx: impl.get_service_dependencies(a["service"], a.get("direction", "both")),
        DependencyInfo.model_validate,
        create_model("get_service_dependencies_args", service=(str, Field(description="Canonical service name")),
                     direction=(Literal["upstream", "downstream", "both"], Field(default="both")))),
    ToolSpec("lookup_stakeholders", "read", frozenset({"notify"}),
             "Stakeholders for a service and severity group (Notification Service only).",
             lambda a, ctx: impl.lookup_stakeholders(a["service"], a["severity_group"])),
    ToolSpec("page_oncall", "dispatch", frozenset({"dispatch_fast_page", "dispatch_page", "rules_only_dispatch"}),
             "Simulated page.", lambda a, ctx: pager_mock.page_oncall(**a)),
    ToolSpec("itsm_upsert_incident", "dispatch", frozenset({"itsm_upsert", "rules_only_dispatch"}),
             "Simulated ITSM create or update.", lambda a, ctx: itsm_mock.itsm_upsert_incident(**a)),
    ToolSpec("itsm_assign", "dispatch", frozenset({"dispatch_itsm_assign", "rules_only_dispatch"}),
             "Simulated ITSM assignment.", lambda a, ctx: itsm_mock.itsm_assign(**a)),
    ToolSpec("send_chat_alert", "dispatch", frozenset({"notify"}), "Simulated chat message.",
             lambda a, ctx: chat_mock.send_chat_alert(**a)),
    ToolSpec("send_email", "dispatch", frozenset({"notify"}), "Simulated email.",
             lambda a, ctx: email_mock.send_email(**a)),
]


# ----------------------------------------------------------------- registry

@dataclass
class ToolRegistry:
    specs: dict[str, ToolSpec] = field(default_factory=lambda: {s.name: s for s in SPECS})
    fallback_active: bool = False
    _bridge: Any = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    # --- policy

    def get(self, name: str, caller: str, ctx: Optional[ToolContext] = None) -> ToolSpec:
        """The tool spec if `caller` may use it; otherwise log tool_access_blocked and raise ToolAccessDenied."""
        spec = self.specs.get(name)
        reason = None
        if spec is None:
            reason = "tool is not registered"
        elif spec.kind == "dispatch":
            if caller not in spec.allowed_callers:
                reason = "dispatch tools are callable only by designated workflow nodes"
        elif caller not in spec.allowed_callers and not (caller in WORKFLOW_NODES and name != "lookup_stakeholders"):
            reason = "tool is not in this caller's allowed set"
        if reason:
            log_error("tool_access_blocked", component="tool_registry", tool=name, caller=caller, reason=reason,
                      incident_id=(ctx and ctx.incident_id) or "unknown", run_id=(ctx and ctx.run_id) or "unknown")
            raise ToolAccessDenied(name, caller, reason)
        return spec

    def allowed_tools(self, caller: str) -> list[str]:
        return [name for name, spec in self.specs.items() if spec.kind == "read" and caller in spec.allowed_callers]

    # --- transport

    @property
    def transport(self) -> str:
        return "inprocess" if self.fallback_active else config.get_settings().tool_transport

    def _mcp(self):
        with self._lock:
            if self._bridge is not None and self._bridge.running:
                return self._bridge
            from src.tools.mcp_bridge import McpBridge

            bridge = self._bridge or McpBridge()
            try:
                names = bridge.start()
            except Exception as exc:  # noqa: BLE001 - FR-73: fall back to in-process tools
                self.fallback_active = True
                self._bridge = None
                log_error("mcp_server_error", component="tool_registry", exc=exc,
                          action="falling back to in-process tools for the rest of the process")
                return None
            self._bridge = bridge
            log_interaction("mcp_server_started", component="tool_registry", tool_count=len(names),
                            tools=sorted(names))
            return bridge

    def close(self) -> None:
        with self._lock:
            if self._bridge is not None:
                self._bridge.close()
                self._bridge = None

    # --- calls

    @staticmethod
    def _complete_args(spec: ToolSpec, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        args = {k: v for k, v in args.items() if v is not None}
        if spec.telemetry:
            # The run context comes from the registry, never from the caller (or the model).
            args = {k: v for k, v in args.items() if k not in CONTEXT_KEYS}
            for key, default in (("window_start", ctx.window_start), ("window_end", ctx.window_end)):
                if key not in args:
                    if default is None:
                        raise RecoverableError(f"{spec.name} needs {key} and the run has no incident window")
                    args[key] = default
                if isinstance(args[key], datetime):
                    args[key] = args[key].isoformat()
        return args

    def call(self, name: str, args: dict[str, Any], caller: str, ctx: Optional[ToolContext] = None) -> Any:
        ctx = ctx or ToolContext()
        spec = self.get(name, caller, ctx)
        args = self._complete_args(spec, dict(args), ctx)
        transport = "inprocess"
        started = time.perf_counter()
        error: Optional[str] = None
        try:
            bridge = self._mcp() if spec.mcp and self.transport == "mcp" else None
            if bridge is not None:
                transport = "mcp"
                mcp_args = dict(args)
                if spec.telemetry:
                    mcp_args.update(scenario_id=ctx.scenario_id, incident_id=ctx.incident_id, run_id=ctx.run_id,
                                    withheld_sources=list(ctx.withheld_sources))
                result = spec.parse(bridge.call(name, mcp_args))
            else:
                result = spec.run(args, ctx)
            return result
        except RecoverableError as exc:
            error = str(exc)
            raise
        except (TypeError, ValueError, KeyError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise InvalidToolArguments(f"{name} called with invalid arguments: {type(exc).__name__}") from exc
        finally:
            fields = {"tool": name, "caller": caller, "transport": transport,
                      "latency_ms": round((time.perf_counter() - started) * 1000),
                      "incident_id": ctx.incident_id or "unknown", "run_id": ctx.run_id or "unknown"}
            if error:
                log_error("tool_call", component=caller, error=error, **fields)
            else:
                log_interaction("tool_call", component=caller, **fields)

    def call_for_llm(self, name: str, args: dict[str, Any], caller: str, ctx: ToolContext
                     ) -> tuple[str, list[str], Any]:
        """For an LLM tool call: (content for the model, guardrail flags, typed result or None).
        The content is compact JSON (notable items without their full records), or an error
        message the model can read when the tool is not allowed."""
        try:
            spec = self.get(name, caller, ctx)
        except ToolAccessDenied as exc:
            return f"Error: tool {name!r} is not available to you ({exc.reason}).", ["tool_access_blocked"], None
        problem = self._check_llm_args(spec, args)
        if problem:
            return f"Error: invalid arguments for {name}: {problem}. Correct them and try again.", [], None
        try:
            result = self.call(name, args, caller, ctx)
        except InvalidToolArguments as exc:
            return f"Error: {exc}. Correct the arguments and try again.", [], None
        return compact_json(result), [], result

    @staticmethod
    def _check_llm_args(spec: ToolSpec, args: dict[str, Any]) -> Optional[str]:
        """A model's arguments are checked before the call, so a mistake goes back to the model
        instead of failing the node (data failures still fail it, FR-36)."""
        from pydantic import ValidationError

        from src.tools.implementations.get_service_dependencies import load_dependency_map

        clean = {k: v for k, v in args.items() if k not in CONTEXT_KEYS}
        if spec.binding is not None:
            try:
                spec.binding.model_validate(clean)
            except ValidationError as exc:
                return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3])
        services = clean.get("service")
        names = [services] if isinstance(services, str) else list(services or [])
        unknown = [s for s in names if s not in load_dependency_map()]
        if unknown:
            return f"unknown service {', '.join(unknown)}; use canonical names such as payments-service"
        return None


    def as_langchain_tools(self, agent: str) -> list:
        """Tool schemas for `bind_tools`. Executing them directly is not allowed: the agent loop
        sends every tool call through `call_for_llm`, which adds the run context."""
        from langchain_core.tools import StructuredTool

        def refuse(**_: Any) -> str:
            raise RuntimeError("call tools through ToolRegistry.call_for_llm")

        return [StructuredTool.from_function(func=refuse, name=spec.name, description=spec.description,
                                             args_schema=spec.binding)
                for spec in self.specs.values()
                if spec.kind == "read" and agent in spec.allowed_callers and spec.binding is not None]


def compact_json(result: Any) -> str:
    """Tool result as JSON for a prompt: full records are left out (the summaries carry the facts)."""
    def one(item: Any) -> Any:
        if isinstance(item, ToolSummary):
            data = item.model_dump(mode="json", exclude={"notable": {"__all__": {"record"}}})
            analysis = data.get("complaint_analysis")
            if analysis:
                for cluster in analysis["clusters"]:
                    cluster["complaint_ids"] = cluster["complaint_ids"][:3]
                analysis["clusters"] = analysis["clusters"][:8]
            return {k: v for k, v in data.items() if v not in (None, [], {}) or k == "notable"}
        return item.model_dump(mode="json") if isinstance(item, BaseModel) else item

    payload = [one(r) for r in result] if isinstance(result, list) else one(result)
    return json.dumps(payload, ensure_ascii=False)


_registry: Optional[ToolRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> ToolRegistry:
    """The process-wide registry (one MCP session per process)."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ToolRegistry()
        return _registry

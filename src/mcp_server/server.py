"""The `incident-tools` MCP server (Architecture Spec Section 7.5; Req. FR-72).

Serves exactly the read-only tools in MCP_EXPOSED_TOOLS over stdio. Each tool
is a thin wrapper around the same implementation the in-process registry uses;
no tool logic lives here. Dispatch tools and lookup_stakeholders are never
registered. The server accepts a scenario ID, never a file path.

Run: python -m src.mcp_server.server   (stdout carries the MCP protocol: never print)
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from src import config
from src.safety.redaction import register_secret_provider
from src.tools import implementations as impl
from src.tools.implementations import ToolContext

MCP_EXPOSED_TOOLS: tuple[str, ...] = (
    "search_knowledge_base", "query_logs", "query_kafka_events", "query_api_metrics", "query_db_infra_metrics",
    "query_network", "query_change_records", "query_complaints", "query_connect_status", "query_acl_audit",
    "query_schema_registry", "query_cluster_quorum", "get_service_dependencies",
)

server = FastMCP("incident-tools", log_level="WARNING")


def _ctx(scenario_id: str, incident_id: Optional[str], run_id: Optional[str],
         withheld_sources: Optional[list[str]]) -> ToolContext:
    return ToolContext(scenario_id=scenario_id, incident_id=incident_id, run_id=run_id,
                       withheld_sources=tuple(withheld_sources or ()))


def _out(result: Any) -> dict[str, Any]:
    """Every tool returns {"result": ...} as JSON, so lists and models travel the same way."""
    if isinstance(result, list):
        return {"result": [item.model_dump(mode="json") for item in result]}
    return {"result": result.model_dump(mode="json")}


@server.tool()
def search_knowledge_base(query: str, top_k: int = 5, doc_types: Optional[list[str]] = None,
                          include_unverified: bool = False) -> dict[str, Any]:
    """Search runbooks, postmortems, verified resolutions, service docs and regulatory notes. Returns the best
    matching sections with document ID, section, excerpt and last_verified date."""
    return _out(impl.search_knowledge_base(query, top_k, doc_types, include_unverified))


@server.tool()
def query_logs(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
               level: Optional[str] = None, error_code: Optional[str] = None, incident_id: Optional[str] = None,
               run_id: Optional[str] = None, withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Application logs: counts by level and error code and the most frequent messages."""
    return _out(impl.query_logs(_ctx(scenario_id, incident_id, run_id, withheld_sources), service, window_start,
                                window_end, level=level, error_code=error_code))


@server.tool()
def query_kafka_events(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                       topic: Optional[str] = None, event_type: Optional[str] = None,
                       incident_id: Optional[str] = None, run_id: Optional[str] = None,
                       withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Kafka events: consumer lag, rebalances, broker failures, under-replicated partitions, duplicate
    transaction IDs."""
    return _out(impl.query_kafka_events(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                        window_start, window_end, topic=topic, event_type=event_type))


@server.tool()
def query_api_metrics(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                      endpoint: Optional[str] = None, incident_id: Optional[str] = None, run_id: Optional[str] = None,
                      withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """API metrics per service and endpoint: max error rate, max p95 latency and baseline p95."""
    return _out(impl.query_api_metrics(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                       window_start, window_end, endpoint=endpoint))


@server.tool()
def query_db_infra_metrics(scenario_id: str, window_start: str, window_end: str,
                           service: Optional[list[str]] = None, db_instance: Optional[str] = None,
                           incident_id: Optional[str] = None, run_id: Optional[str] = None,
                           withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Database and infrastructure metrics: connection use, CPU, slow queries, lock waits."""
    return _out(impl.query_db_infra_metrics(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                            window_start, window_end, db_instance=db_instance))


@server.tool()
def query_network(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                  destination: Optional[str] = None, incident_id: Optional[str] = None, run_id: Optional[str] = None,
                  withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Network events: TLS handshake failures by route, latency, certificate expiry dates."""
    return _out(impl.query_network(_ctx(scenario_id, incident_id, run_id, withheld_sources), service, window_start,
                                   window_end, destination=destination))


@server.tool()
def query_change_records(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                         lookback_min: int = 180, change_type: Optional[str] = None,
                         incident_id: Optional[str] = None, run_id: Optional[str] = None,
                         withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Change records (releases, configuration, infrastructure, ACL, schema) from lookback_min minutes before
    the window start to its end."""
    return _out(impl.query_change_records(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                          window_start, window_end, lookback_min=lookback_min,
                                          change_type=change_type))


@server.tool()
def query_complaints(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                     product: Optional[str] = None, incident_id: Optional[str] = None, run_id: Optional[str] = None,
                     withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Customer complaints: clusters by product and topic, volume, sentiment, impact and earliest signal."""
    return _out(impl.query_complaints(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                      window_start, window_end, product=product))


@server.tool()
def query_connect_status(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                         connector: Optional[str] = None, incident_id: Optional[str] = None,
                         run_id: Optional[str] = None, withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Kafka Connect connector task states and failures."""
    return _out(impl.query_connect_status(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                          window_start, window_end, connector=connector))


@server.tool()
def query_acl_audit(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                    resource: Optional[str] = None, incident_id: Optional[str] = None, run_id: Optional[str] = None,
                    withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Kafka ACL audit: denied operations by resource and related change references."""
    return _out(impl.query_acl_audit(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                     window_start, window_end, resource=resource))


@server.tool()
def query_schema_registry(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                          subject: Optional[str] = None, incident_id: Optional[str] = None,
                          run_id: Optional[str] = None, withheld_sources: Optional[list[str]] = None
                          ) -> dict[str, Any]:
    """Schema Registry: compatibility errors by subject."""
    return _out(impl.query_schema_registry(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                           window_start, window_end, subject=subject))


@server.tool()
def query_cluster_quorum(scenario_id: str, window_start: str, window_end: str, service: Optional[list[str]] = None,
                         incident_id: Optional[str] = None, run_id: Optional[str] = None,
                         withheld_sources: Optional[list[str]] = None) -> dict[str, Any]:
    """Kafka cluster quorum: health, session expirations, offline partitions, controller changes."""
    return _out(impl.query_cluster_quorum(_ctx(scenario_id, incident_id, run_id, withheld_sources), service,
                                          window_start, window_end))


@server.tool()
def get_service_dependencies(service: str, direction: str = "both") -> dict[str, Any]:
    """Service topology: what a service depends on, what depends on it, and the customer-facing blast radius."""
    return _out(impl.get_service_dependencies(service, direction))  # type: ignore[arg-type]


def main() -> None:
    register_secret_provider(config.get_settings().secret_values)
    # Import the retrieval stack (FAISS, numpy) now, inside the startup budget, so the first
    # search does not exceed the per-call timeout.
    from src.tool_retrieval import retriever  # noqa: F401

    server.run(transport="stdio")


if __name__ == "__main__":
    main()

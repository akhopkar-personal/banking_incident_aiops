"""Read-tool implementations (Architecture Spec Section 7.3). The MCP server and
the in-process registry both call these functions, so both transports return
the same result."""

from ._common import ToolContext
from .get_service_dependencies import get_service_dependencies
from .lookup_stakeholders import lookup_stakeholders
from .query_acl_audit import query_acl_audit
from .query_api_metrics import query_api_metrics
from .query_change_records import query_change_records
from .query_cluster_quorum import query_cluster_quorum
from .query_complaints import query_complaints
from .query_connect_status import query_connect_status
from .query_db_infra_metrics import query_db_infra_metrics
from .query_kafka_events import query_kafka_events
from .query_logs import query_logs
from .query_network import query_network
from .query_schema_registry import query_schema_registry
from .search_knowledge_base import search_knowledge_base

__all__ = [
    "ToolContext", "get_service_dependencies", "lookup_stakeholders", "query_acl_audit", "query_api_metrics",
    "query_change_records", "query_cluster_quorum", "query_complaints", "query_connect_status",
    "query_db_infra_metrics", "query_kafka_events", "query_logs", "query_network", "query_schema_registry",
    "search_knowledge_base",
]

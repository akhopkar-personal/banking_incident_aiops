"""Telemetry records, evidence items and complaint analysis
(Architecture Spec Sections 3.4, 3.5; Req. Section 9.2)."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import Field

from .common import EvidenceId, Schema, UtcDatetime
from .enums import ChangeType, TelemetrySource


class TelemetryRecord(Schema):
    """Fields every telemetry source shares."""

    timestamp: UtcDatetime
    service: str
    environment: str = "prod"
    trace_id: Optional[str] = None
    transaction_id: Optional[str] = None
    record_id: EvidenceId


class LogRecord(TelemetryRecord):
    level: str
    message: str
    error_code: Optional[str] = None
    host: Optional[str] = None


class KafkaEventRecord(TelemetryRecord):
    topic: str
    partition: int
    consumer_group: Optional[str] = None
    lag: Optional[int] = None
    event_type: Literal["produced", "consumed", "rebalance", "broker_down", "metric_sample"]
    error: Optional[str] = None
    isr_count: Optional[int] = None
    under_replicated: Optional[int] = None


class ApiMetricRecord(TelemetryRecord):
    endpoint: str
    request_count: int = Field(ge=0)
    error_rate: float = Field(ge=0.0, le=1.0)
    p95_latency_ms: float = Field(ge=0.0)
    status_code_breakdown: dict[str, int] = Field(default_factory=dict)


class DbInfraMetricRecord(TelemetryRecord):
    db_instance: str
    active_connections: int = Field(ge=0)
    max_connections: int = Field(gt=0)
    cpu_pct: float = Field(ge=0.0, le=100.0)
    mem_pct: float = Field(ge=0.0, le=100.0)
    slow_query_count: int = Field(ge=0)
    lock_wait_ms: float = Field(ge=0.0)


class NetworkEventRecord(TelemetryRecord):
    source: str
    destination: str
    route: Optional[str] = None
    latency_ms: float = Field(ge=0.0)
    packet_loss_pct: float = Field(ge=0.0, le=100.0)
    tls_status: Literal["ok", "handshake_failed", "expired"]
    cert_expiry: Optional[UtcDatetime] = None


class ChangeRecord(TelemetryRecord):
    deployment_id: str
    version: Optional[str] = None
    change_type: ChangeType
    author: str
    change_summary: str
    rollback_available: bool


class ComplaintRecord(TelemetryRecord):
    complaint_id: str
    channel: str
    text: str
    product: str
    customer_ref: str


class ConnectStatusRecord(TelemetryRecord):
    connector: str
    task_id: int
    state: Literal["RUNNING", "FAILED", "PAUSED"]
    trace_excerpt: Optional[str] = None


class AclAuditRecord(TelemetryRecord):
    principal: str
    resource: str
    operation: str
    result: Literal["ALLOWED", "DENIED"]
    change_ref: Optional[str] = None


class SchemaRegistryRecord(TelemetryRecord):
    subject: str
    version: int
    compatibility: str
    error: Optional[str] = None


class ClusterQuorumRecord(TelemetryRecord):
    mode: Literal["zookeeper", "kraft"]
    controller_id: int
    quorum_healthy: bool
    session_expirations: int = Field(ge=0)
    offline_partitions: int = Field(ge=0)


# Record model for each evidence ID prefix.
RECORD_MODELS: dict[TelemetrySource, type[TelemetryRecord]] = {
    TelemetrySource.LOG: LogRecord,
    TelemetrySource.KFK: KafkaEventRecord,
    TelemetrySource.API: ApiMetricRecord,
    TelemetrySource.DBM: DbInfraMetricRecord,
    TelemetrySource.NET: NetworkEventRecord,
    TelemetrySource.DEP: ChangeRecord,
    TelemetrySource.CMP: ComplaintRecord,
    TelemetrySource.CON: ConnectStatusRecord,
    TelemetrySource.ACL: AclAuditRecord,
    TelemetrySource.SRG: SchemaRegistryRecord,
    TelemetrySource.QRM: ClusterQuorumRecord,
}

AnyRecord = Union[
    LogRecord, KafkaEventRecord, ApiMetricRecord, DbInfraMetricRecord, NetworkEventRecord,
    ChangeRecord, ComplaintRecord, ConnectStatusRecord, AclAuditRecord,
    SchemaRegistryRecord, ClusterQuorumRecord,
]


def source_of(evidence_id: str) -> TelemetrySource:
    """Telemetry source for an evidence ID such as 'LOG-0421'."""
    return TelemetrySource(evidence_id.split("-", 1)[0])


class EvidenceItem(Schema):
    evidence_id: EvidenceId
    source: TelemetrySource
    service: str
    timestamp: UtcDatetime
    summary: str
    record: dict[str, Any] = Field(default_factory=dict)


class ToolSummary(Schema):
    tool: str
    service_scope: list[str] = Field(default_factory=list)
    window_start: UtcDatetime
    window_end: UtcDatetime
    record_count: int = Field(ge=0)
    aggregates: dict[str, float] = Field(default_factory=dict)
    notable: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    truncated: bool = False


class ComplaintCluster(Schema):
    cluster_id: str
    topic: str
    product: str
    complaint_ids: list[str] = Field(default_factory=list)
    first_seen: UtcDatetime
    volume: int = Field(ge=0)
    sentiment_score: float = Field(ge=-1.0, le=1.0)


class ComplaintAnalysis(Schema):
    clusters: list[ComplaintCluster] = Field(default_factory=list)
    total_complaints_in_window: int = Field(ge=0)
    estimated_customer_impact: str
    earliest_signal_time: Optional[UtcDatetime] = None

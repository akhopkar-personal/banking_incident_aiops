"""Shared enums (Architecture Spec Section 3.1)."""

from __future__ import annotations

from enum import Enum


class InputMode(str, Enum):
    DETECTION_REPLAY = "detection_replay"
    ALERT_JSON = "alert_json"
    FREE_TEXT = "free_text"


class SeverityLevel(str, Enum):
    """S1 is the most severe."""

    S1 = "S1"
    S2 = "S2"
    S3 = "S3"
    S4 = "S4"

    @property
    def rank(self) -> int:
        """1 for S1 (most severe) to 4 for S4."""
        return int(self.value[1])

    @property
    def group(self) -> "SeverityGroup":
        return SeverityGroup.MAJOR if self.rank <= 2 else SeverityGroup.LOW_MEDIUM

    def is_more_severe_than(self, other: "SeverityLevel") -> bool:
        return self.rank < other.rank


def most_severe(*levels: SeverityLevel) -> SeverityLevel:
    """Return the most severe of the given levels; S4 if none are given."""
    present = [level for level in levels if level is not None]
    if not present:
        return SeverityLevel.S4
    return min(present, key=lambda level: level.rank)


class SeverityGroup(str, Enum):
    MAJOR = "major"
    LOW_MEDIUM = "low_medium"


class IssueClass(str, Enum):
    RELEASE_REGRESSION = "release_regression"
    CONFIG_CHANGE = "config_change"
    EVENTHUB_BROKER = "eventhub_broker"
    EVENTHUB_CONNECT = "eventhub_connect"
    EVENTHUB_ACL = "eventhub_acl"
    EVENTHUB_SCHEMA = "eventhub_schema"
    DATABASE_CAPACITY = "database_capacity"
    NETWORK_TLS = "network_tls"
    DATA_INTEGRITY = "data_integrity"
    OTHER = "other"


class ServiceRole(str, Enum):
    ORIGIN = "origin"
    IMPACTED = "impacted"
    DOWNSTREAM = "downstream"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class InvestigationStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class IncidentState(str, Enum):
    OPEN = "OPEN"
    PAGED = "PAGED"
    ASSIGNED = "ASSIGNED"
    RESOLVED = "RESOLVED"
    CLOSED_VERIFIED = "CLOSED_VERIFIED"
    CLOSED_UNVERIFIED = "CLOSED_UNVERIFIED"


class TelemetrySource(str, Enum):
    """Values are the evidence ID prefixes (Req. Section 9.2)."""

    LOG = "LOG"
    KFK = "KFK"
    API = "API"
    DBM = "DBM"
    NET = "NET"
    DEP = "DEP"
    CMP = "CMP"
    CON = "CON"
    ACL = "ACL"
    SRG = "SRG"
    QRM = "QRM"


class ChangeType(str, Enum):
    RELEASE = "release"
    CONFIG = "config"
    INFRA = "infra"
    ACL = "acl"
    SCHEMA = "schema"
    CONNECTOR_CONFIG = "connector_config"


class IssueType(str, Enum):
    SEVERITY_ERROR = "severity_error"
    ROOT_CAUSE_ERROR = "root_cause_error"
    CITATION_ERROR = "citation_error"
    MISSING_EVIDENCE = "missing_evidence"
    RETRIEVAL_MISS = "retrieval_miss"
    TRIAGE_CLASS_ERROR = "triage_class_error"
    CHANGE_CORRELATION_MISS = "change_correlation_miss"
    VERBOSITY = "verbosity"


class ReviewerRole(str, Enum):
    ONCALL = "oncall"
    ESCALATION = "escalation"
    PRODUCTION_SUPPORT = "production_support"
    INCIDENT_COMMANDER = "incident_commander"
    SME = "sme"
    EVALUATION_ENGINEER = "evaluation_engineer"


class SignalType(str, Enum):
    REVIEW = "review"
    RATING = "rating"
    PAIRWISE = "pairwise"
    RECLASSIFICATION = "reclassification"
    GATE_HANDOFF = "gate_handoff"
    FIX_OUTCOME = "fix_outcome"
    VERIFICATION = "verification"


class FixOutcome(str, Enum):
    WORKED = "worked"
    PARTLY = "partly"
    DID_NOT_WORK = "did_not_work"
    NOT_TRIED = "not_tried"


class ErrorType(str, Enum):
    TOOL_CALL_FAILED = "tool_call_failed"
    LLM_CALL_FAILED = "llm_call_failed"
    LLM_UNAVAILABLE = "llm_unavailable"
    KILL_SWITCH = "kill_switch"
    COST_CAP_EXCEEDED = "cost_cap_exceeded"
    SCHEMA_INVALID = "schema_invalid"
    UNHANDLED_EXCEPTION = "unhandled_exception"


class RunMode(str, Enum):
    LIVE = "live"
    EVALUATION = "evaluation"

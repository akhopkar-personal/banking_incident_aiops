"""Shared field types and patterns used by the schema modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


# Timezone-aware datetime, normalized to UTC. Naive datetimes are rejected:
# intake decides how to interpret a time without a timezone (Req. 6.3).
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Rating = Annotated[int, Field(ge=1, le=5)]

INCIDENT_ID_PATTERN = r"^INC-\d{8}-\d{3}$"
RUN_ID_PATTERN = r"^RUN-\d+$"
ANOMALY_ID_PATTERN = r"^ANM-\d+$"
EVIDENCE_ID_PATTERN = r"^(LOG|KFK|API|DBM|NET|DEP|CMP|CON|ACL|SRG|QRM)-\d+$"

IncidentId = Annotated[str, Field(pattern=INCIDENT_ID_PATTERN)]
RunId = Annotated[str, Field(pattern=RUN_ID_PATTERN)]
AnomalyId = Annotated[str, Field(pattern=ANOMALY_ID_PATTERN)]
EvidenceId = Annotated[str, Field(pattern=EVIDENCE_ID_PATTERN)]


def _as_list(value: object) -> object:
    """LLMs often answer a list-of-strings field with one string: accept it as a one-item list
    (an empty string as an empty list)."""
    if isinstance(value, str):
        return [value] if value.strip() else []
    return value


# For list-of-string fields an LLM fills in (Architecture Spec Section 6.1).
LenientStrList = Annotated[list[str], BeforeValidator(_as_list)]
LenientEvidenceIds = Annotated[list[EvidenceId], BeforeValidator(_as_list)]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Schema(BaseModel):
    """Base for all schemas: reject unknown fields so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")

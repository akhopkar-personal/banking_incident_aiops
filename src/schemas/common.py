"""Shared field types and patterns used by the schema modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Schema(BaseModel):
    """Base for all schemas: reject unknown fields so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")

"""Incident Object and anomaly event (Architecture Spec Sections 3.2, 3.3)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import AnomalyId, EvidenceId, IncidentId, RunId, Schema, UtcDatetime
from .enums import InputMode, SeverityLevel


class AnomalyEvent(Schema):
    anomaly_id: AnomalyId
    service: str
    signal: str
    observed: float
    baseline: float
    z_score: Optional[float] = None
    detected_at: UtcDatetime
    evidence_ids: list[EvidenceId] = Field(default_factory=list)
    method: Literal["zscore", "threshold"]


class IncidentObject(Schema):
    incident_id: IncidentId
    run_id: RunId
    idempotency_key: str = Field(min_length=1)
    input_mode: InputMode
    scenario_id: Optional[str] = None
    raw_input: str
    anomaly_events: list[AnomalyEvent] = Field(default_factory=list)
    reported_service: Optional[str] = None
    reported_severity: Optional[SeverityLevel] = None
    reference_time: UtcDatetime
    window_start: UtcDatetime
    window_end: UtcDatetime
    hints: list[str] = Field(default_factory=list)
    timezone_assumed_utc: bool = False
    # Evidence prefixes (for example "DEP") unavailable for this run: the golden
    # missing-source variant (Architecture Spec Section 16.4). Empty in live runs.
    withheld_sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _window_order(self) -> "IncidentObject":
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self

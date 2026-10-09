"""Shared helpers for the read tools (Architecture Spec Section 7.2).

Every read tool takes a `ToolContext` (which scenario's files to read, and the
incident and run it serves), a service scope and a time window. It returns a
`ToolSummary`: aggregates plus at most 20 notable records, redacted. Missing
or malformed files raise `RecoverableError`.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence, Union

from pydantic import ValidationError

from src import config
from src.errors import RecoverableError
from src.safety.redaction import redact, redact_complaint, redact_record
from src.schemas.enums import TelemetrySource
from src.schemas.evidence import RECORD_MODELS, EvidenceItem, ToolSummary

MAX_NOTABLE = 20

SOURCE_FILES: dict[str, str] = {
    "LOG": "logs/app_logs.json",
    "KFK": "kafka/kafka_events.json",
    "API": "api_metrics/api_metrics.csv",
    "DBM": "db_infra_metrics/db_infra_metrics.csv",
    "NET": "network/network_events.json",
    "DEP": "deployments/deployments.json",
    "CMP": "complaints/complaints.json",
    "CON": "eventhub/connect_status.json",
    "ACL": "eventhub/acl_audit.json",
    "SRG": "eventhub/schema_registry.json",
    "QRM": "eventhub/cluster_quorum.json",
}

ServiceArg = Union[str, Sequence[str], None]
TimeArg = Union[str, datetime]


@dataclass(frozen=True)
class ToolContext:
    """The run a tool call serves. The scenario is chosen by ID, never by path."""

    scenario_id: Optional[str] = None
    incident_id: Optional[str] = None
    run_id: Optional[str] = None
    withheld_sources: tuple[str, ...] = field(default_factory=tuple)
    # The incident window: used when a tool call does not give its own.
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None

    @property
    def scenario_dir(self) -> Path:
        if not self.scenario_id:
            raise RecoverableError("this run has no scenario data (scenario_id is not set)")
        return config.get_settings().scenario_dir(self.scenario_id)


@dataclass(frozen=True)
class Row:
    """One validated record with its parsed timestamp."""

    ts: datetime
    data: dict[str, Any]

    @property
    def service(self) -> str:
        return self.data["service"]

    @property
    def record_id(self) -> str:
        return self.data["record_id"]


def parse_time(value: TimeArg) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def service_list(service: ServiceArg) -> Optional[list[str]]:
    """None means every service."""
    if service is None or service == "" or service == []:
        return None
    if isinstance(service, str):
        return [s.strip() for s in service.split(",") if s.strip()]
    return list(service)


# ------------------------------------------------------------------ loading

def _parse_csv(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8", newline="") as handle:
        rows = []
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = {k: v for k, v in raw.items() if v != ""}
            if "status_code_breakdown" in row:
                row["status_code_breakdown"] = json.loads(row["status_code_breakdown"])
            rows.append(row)
        return rows


@lru_cache(maxsize=128)
def _load(path_text: str, _mtime: float, src: str) -> tuple[Row, ...]:
    path = Path(path_text)
    try:
        raw = _parse_csv(path) if path.suffix == ".csv" else json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoverableError(f"cannot read {src} data: {type(exc).__name__}") from exc
    model = RECORD_MODELS[TelemetrySource(src)]
    rows = []
    for index, item in enumerate(raw):
        try:
            record = model.model_validate(item)
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
            raise RecoverableError(f"malformed {src} record at position {index} (fields: {', '.join(fields)})") from exc
        rows.append(Row(record.timestamp, record.model_dump(mode="json", exclude_none=True)))
    return tuple(rows)


def load_rows(ctx: ToolContext, src: str) -> tuple[Row, ...]:
    """All validated records of one source, cached per file version."""
    path = ctx.scenario_dir / SOURCE_FILES[src]
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise RecoverableError(f"{src} data file is missing for scenario {ctx.scenario_id}") from exc
    return _load(str(path), mtime, src)


def select(ctx: ToolContext, src: str, window_start: TimeArg, window_end: TimeArg, service: ServiceArg = None,
           where: Optional[Callable[[dict[str, Any]], bool]] = None) -> list[Row]:
    """Records of `src` with window_start <= timestamp < window_end, for the given services."""
    start, end = parse_time(window_start), parse_time(window_end)
    services = service_list(service)
    return [r for r in load_rows(ctx, src)
            if start <= r.ts < end and (services is None or r.service in services) and (where is None or where(r.data))]


# ------------------------------------------------------------------ output

def evidence(src: str, row: Row, summary: str) -> EvidenceItem:
    """An evidence item with the record and summary redacted."""
    record, _ = redact_record(row.data)
    if src == "CMP" and "text" in record:
        record["text"] = redact_complaint(row.data["text"])[0]
    text = redact_complaint(summary)[0] if src == "CMP" else redact(summary)[0]
    return EvidenceItem(evidence_id=row.record_id, source=TelemetrySource(src), service=row.service,
                        timestamp=row.ts, summary=text[:300], record=record)


def pick_notable(candidates: Iterable[tuple[float, EvidenceItem]], limit: int = MAX_NOTABLE
                 ) -> tuple[list[EvidenceItem], bool]:
    """Highest priority first (then earliest), without duplicates; (items, truncated)."""
    ordered = sorted(candidates, key=lambda c: (-c[0], c[1].timestamp, c[1].evidence_id))
    seen: set[str] = set()
    unique = []
    for _, item in ordered:
        if item.evidence_id not in seen:
            seen.add(item.evidence_id)
            unique.append(item)
    return unique[:limit], len(unique) > limit


def summary(tool: str, ctx: ToolContext, src: Optional[str], service: ServiceArg, window_start: TimeArg,
            window_end: TimeArg, record_count: int, aggregates: dict[str, float],
            candidates: Iterable[tuple[float, EvidenceItem]] = (), **extra: Any) -> ToolSummary:
    notable, truncated = pick_notable(candidates)
    return ToolSummary(tool=tool, service_scope=service_list(service) or [], window_start=parse_time(window_start),
                       window_end=parse_time(window_end), record_count=record_count,
                       aggregates={k: round(float(v), 4) for k, v in aggregates.items()}, notable=notable,
                       truncated=truncated, **extra)


def withheld(tool: str, ctx: ToolContext, src: str, service: ServiceArg, window_start: TimeArg,
             window_end: TimeArg) -> Optional[ToolSummary]:
    """An empty summary marked unavailable when `src` is withheld for this run, else None."""
    if src not in ctx.withheld_sources:
        return None
    return summary(tool, ctx, src, service, window_start, window_end, 0, {}, source_unavailable=True)


def minute_buckets(rows: Iterable[Row]) -> dict[datetime, list[Row]]:
    buckets: dict[datetime, list[Row]] = {}
    for row in rows:
        buckets.setdefault(row.ts.replace(second=0, microsecond=0), []).append(row)
    return buckets


def clear_cache() -> None:
    _load.cache_clear()

"""ScenarioBuilder: collects synthetic telemetry records for one scenario
directory, assigns evidence IDs and writes the files (Architecture Spec Section 16;
Req. Section 9.2).

Records are plain dicts. Keys starting with "_" are private to the generator
(minute offset, tag, fixed ID) and are never written. Time is measured in
minutes from the start of the scenario window; negative minutes are before it.
"""

from __future__ import annotations

import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

# Evidence prefix -> file inside the scenario directory (Req. Section 9.2).
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

COMMON_FIELDS = ["timestamp", "record_id", "service", "environment", "trace_id", "transaction_id"]

# CSV sources have a fixed column order. Dict-valued fields are written as JSON text.
CSV_COLUMNS: dict[str, list[str]] = {
    "API": COMMON_FIELDS + ["endpoint", "request_count", "error_rate", "p95_latency_ms", "status_code_breakdown"],
    "DBM": COMMON_FIELDS + ["db_instance", "active_connections", "max_connections", "cpu_pct", "mem_pct",
                            "slow_query_count", "lock_wait_ms"],
}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ScenarioBuilder:
    def __init__(self, scenario_id: str, start: datetime, seed: int, minutes: int = 90):
        self.scenario_id = scenario_id
        self.start = start
        self.minutes = minutes
        self.seed = seed
        self.rng = random.Random(seed)
        self.records: dict[str, list[dict[str, Any]]] = {src: [] for src in SOURCE_FILES}
        self.extra_files: dict[str, Any] = {}
        self.manifest: dict[str, Any] = {}
        self.evidence: dict[str, str] = {}  # tag -> evidence ID, filled by finalize()
        self._seq = 0
        self._txn = 0
        self._complaint = 0

    # --- time

    def at(self, minute: float) -> datetime:
        return self.start + timedelta(minutes=minute)

    # --- adding and selecting records

    def add(self, src: str, minute: float, service: Optional[str], *, tag: Optional[str] = None,
            record_id: Optional[str] = None, **fields: Any) -> dict[str, Any]:
        """Add one record at `minute` (fractions give seconds)."""
        self._seq += 1
        record: dict[str, Any] = {"_minute": minute, "_seq": self._seq, "service": service,
                                  "environment": "prod", **fields}
        if tag:
            record["_tag"] = tag
        if record_id:
            record["_id"] = record_id
        self.records[src].append(record)
        return record

    def event_minute(self, minute: int) -> float:
        """A random second inside the given minute (whole seconds)."""
        return minute + self.rng.randint(1, 59) / 60

    def select(self, src: str, service: Optional[str] = None, start: float = float("-inf"),
               end: float = float("inf"), **match: Any) -> list[dict[str, Any]]:
        """Records of `src` for `service` with start <= minute < end and matching fields."""
        return [
            r for r in self.records[src]
            if (service is None or r["service"] == service)
            and start <= r["_minute"] < end
            and all(r.get(k) == v for k, v in match.items())
        ]

    # --- identifiers

    def next_txn(self) -> str:
        self._txn += 1
        return f"TXN-{self.start:%Y%m%d}-{self._txn:06d}"

    def next_complaint_id(self) -> str:
        self._complaint += 1
        return f"CPL-{self.start:%Y%m%d}-{self._complaint:04d}"

    def trace_id(self) -> str:
        return "tr-" + "".join(self.rng.choice("0123456789abcdef") for _ in range(16))

    def jitter(self, value: float, pct: float = 0.05) -> float:
        return value * self.rng.uniform(1 - pct, 1 + pct)

    # --- finalize and write

    def finalize(self) -> None:
        """Sort each source by time, assign evidence IDs and resolve tags."""
        for src, records in self.records.items():
            records.sort(key=lambda r: (r["_minute"], r["_seq"]))
            number = 0
            for record in records:
                record["timestamp"] = self.at(record["_minute"])
                if "_id" in record:
                    record["record_id"] = record["_id"]
                else:
                    number += 1
                    record["record_id"] = f"{src}-{number:04d}"
                if "_tag" in record:
                    if record["_tag"] in self.evidence:
                        raise ValueError(f"duplicate evidence tag {record['_tag']!r}")
                    self.evidence[record["_tag"]] = record["record_id"]
            ids = [r["record_id"] for r in records]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate record IDs in {src}")
        for record in self.records["API"]:
            record["status_code_breakdown"] = _status_breakdown(record["request_count"], record["error_rate"])

    def public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """The record as written: common fields first, private keys and None values dropped."""
        out: dict[str, Any] = {}
        for key in COMMON_FIELDS:
            if record.get(key) is not None:
                out[key] = iso(record[key]) if isinstance(record[key], datetime) else record[key]
        for key, value in record.items():
            if key.startswith("_") or key in out or key in COMMON_FIELDS or value is None:
                continue
            out[key] = iso(value) if isinstance(value, datetime) else value
        return out

    def write(self, out_dir: Path) -> dict[str, int]:
        """Write every source file, extra files and manifest.json; return record counts by file."""
        counts: dict[str, int] = {}
        for src, rel in SOURCE_FILES.items():
            rows = [self.public_record(r) for r in self.records[src]]
            path = out_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if src in CSV_COLUMNS:
                write_text(path, _csv_text(rows, CSV_COLUMNS[src]))
            else:
                write_text(path, json_lines_array(rows))
            counts[rel] = len(rows)
        for rel, content in self.extra_files.items():
            write_text(out_dir / rel, json.dumps(content, indent=2) + "\n")
        manifest = {**self.manifest, "record_counts": counts}
        write_text(out_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
        return counts


def _status_breakdown(request_count: int, error_rate: float) -> dict[str, int]:
    errors = round(request_count * error_rate)
    client_errors = min(round(request_count * 0.01), request_count - errors)
    return {"2xx": request_count - errors - client_errors, "4xx": client_errors, "5xx": errors}


def _csv_text(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: json.dumps(row[c]) if isinstance(row.get(c), dict) else row.get(c, "")
                         for c in columns})
    return buffer.getvalue()


def json_lines_array(rows: Iterable[dict[str, Any]]) -> str:
    """A JSON array with one record per line: valid JSON, compact, and readable in diffs."""
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    if not lines:
        return "[]\n"
    return "[\n" + ",\n".join(lines) + "\n]\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

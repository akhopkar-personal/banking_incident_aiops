"""Anomaly Detection Service (Architecture Spec Section 5.1; Req. FR-37).

No LLM. Telemetry is aggregated into 1-minute buckets per (service, signal).
A bucket is anomalous if its z-score against the baseline is at least 3 (gauge
signals only, and only when the baseline varies) or a hard threshold from
data/baselines.json is crossed. Anomalous buckets of one series that are at
most `merge_gap_minutes` apart form one AnomalyEvent.
"""

from __future__ import annotations

import json
import operator
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from src import config
from src.errors import RecoverableError
from src.logger_setup import log_error, log_interaction
from src.schemas.incident import AnomalyEvent
from src.tools.implementations._common import SOURCE_FILES, Row, ToolContext, select

OPERATORS = {">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}
COUNT_SIGNALS = {"broker_down", "duplicate_transactions", "tls_failures", "connect_failed_tasks", "acl_denied",
                 "log_error_count", "quorum_unhealthy"}


@lru_cache(maxsize=4)
def _load(path: str, _mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_baselines() -> dict[str, Any]:
    path = config.get_settings().reference_file("baselines.json")
    return _load(str(path), path.stat().st_mtime)


@dataclass
class Series:
    """One (service, signal) series: minute -> value, and minute -> record IDs behind it."""

    service: str
    signal: str
    points: dict[datetime, float] = field(default_factory=dict)
    records: dict[datetime, list[str]] = field(default_factory=lambda: defaultdict(list))

    def put_max(self, minute: datetime, value: float, record_id: str) -> None:
        if minute not in self.points or value > self.points[minute]:
            self.points[minute] = value
        self.records[minute].append(record_id)

    def add(self, minute: datetime, amount: float, record_id: str) -> None:
        self.points[minute] = self.points.get(minute, 0.0) + amount
        self.records[minute].append(record_id)


def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def build_series(ctx: ToolContext, window_start: datetime, window_end: datetime) -> tuple[dict, list[datetime]]:
    """All detector series for the window, and the list of minutes that have telemetry.
    A source that cannot be read is skipped (and logged), so detection and paging still work."""
    data: dict[str, list[Row]] = {}
    for src in SOURCE_FILES:
        if src == "DEP" or src in ctx.withheld_sources:
            data[src] = []
            continue
        try:
            data[src] = select(ctx, src, window_start, window_end)
        except RecoverableError as exc:
            log_error("tool_call", component="anomaly_detector", source=src, error=str(exc),
                      incident_id="unknown", run_id="unknown")
            data[src] = []
    series: dict[tuple[str, str], Series] = {}

    def s(service: str, signal: str) -> Series:
        return series.setdefault((service, signal), Series(service, signal))

    for r in data["API"]:
        m = _minute(r.ts)
        s(r.service, "api_error_rate").put_max(m, r.data["error_rate"], r.record_id)
        s(r.service, "api_p95_ms").put_max(m, r.data["p95_latency_ms"], r.record_id)
    for r in data["DBM"]:
        m = _minute(r.ts)
        s(r.service, "db_connections_used_pct").put_max(m, r.data["active_connections"] / r.data["max_connections"],
                                                        r.record_id)
        s(r.service, "db_cpu_pct").put_max(m, r.data["cpu_pct"], r.record_id)
        s(r.service, "db_lock_wait_ms").put_max(m, r.data["lock_wait_ms"], r.record_id)
        s(r.service, "db_slow_queries").put_max(m, r.data["slow_query_count"], r.record_id)
    seen_txn: set[str] = set()
    for r in data["KFK"]:
        m, d = _minute(r.ts), r.data
        if d.get("under_replicated") is not None:
            s(r.service, "kafka_under_replicated").put_max(m, d["under_replicated"], r.record_id)
        if d.get("lag") is not None:
            s(r.service, "consumer_lag").put_max(m, d["lag"], r.record_id)
        if d["event_type"] == "broker_down":
            s(r.service, "broker_down").add(m, 1, r.record_id)
        if d["event_type"] == "produced" and d.get("transaction_id"):
            if d["transaction_id"] in seen_txn:
                s(r.service, "duplicate_transactions").add(m, 1, r.record_id)
            seen_txn.add(d["transaction_id"])
    for r in data["QRM"]:
        m = _minute(r.ts)
        s(r.service, "quorum_offline_partitions").put_max(m, r.data["offline_partitions"], r.record_id)
        if not r.data["quorum_healthy"]:
            s(r.service, "quorum_unhealthy").add(m, 1, r.record_id)
    for r in data["NET"]:
        m = _minute(r.ts)
        s(r.service, "network_latency_ms").put_max(m, r.data["latency_ms"], r.record_id)
        s(r.service, "network_packet_loss_pct").put_max(m, r.data["packet_loss_pct"], r.record_id)
        if r.data["tls_status"] != "ok":
            s(r.service, "tls_failures").add(m, 1, r.record_id)
    for r in data["CON"]:
        if r.data["state"] == "FAILED":
            s(r.service, "connect_failed_tasks").add(_minute(r.ts), 1, r.record_id)
    for r in data["ACL"]:
        if r.data["result"] == "DENIED":
            s(r.service, "acl_denied").add(_minute(r.ts), 1, r.record_id)
    for r in data["LOG"]:
        if r.data["level"].upper() == "ERROR":
            s(r.service, "log_error_count").add(_minute(r.ts), 1, r.record_id)
    by_product: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in data["CMP"]:
        by_product[(r.service, r.data["product"])].append(r)

    all_minutes = sorted({_minute(r.ts) for rows in data.values() for r in rows})
    if all_minutes:
        span = [all_minutes[0] + timedelta(minutes=i)
                for i in range(int((all_minutes[-1] - all_minutes[0]).total_seconds() // 60) + 1)]
    else:
        span = []
    for (service, product), members in by_product.items():
        complaint_series = s(service, f"complaints_30min:{product}")
        for minute in span:
            recent = [r for r in members if minute - timedelta(minutes=29) <= _minute(r.ts) <= minute]
            complaint_series.points[minute] = float(len(recent))
            if recent and _minute(recent[-1].ts) == minute:
                complaint_series.records[minute].append(recent[-1].record_id)
    # Count signals are zero in minutes without events.
    for series_item in series.values():
        if series_item.signal in COUNT_SIGNALS:
            for minute in span:
                series_item.points.setdefault(minute, 0.0)
    return series, span


def detect(scenario_id: str, window_start: datetime, window_end: datetime, withheld_sources: Iterable[str] = (),
           log: bool = True) -> list[AnomalyEvent]:
    """Anomaly events for the window, ordered by time (Section 5.1)."""
    cfg = load_baselines()
    ctx = ToolContext(scenario_id=scenario_id, withheld_sources=tuple(withheld_sources))
    series, span = build_series(ctx, window_start, window_end)
    if not span:
        return []
    baseline_end = span[0] + timedelta(minutes=cfg["baseline_minutes"])
    zscore_signals = set(cfg["zscore_signals"])
    gap = timedelta(minutes=cfg["merge_gap_minutes"])
    found: list[dict[str, Any]] = []

    for (service, signal), item in sorted(series.items()):
        base_name = signal.split(":", 1)[0]
        threshold = cfg["hard_thresholds"].get(base_name)
        base_values = [v for m, v in item.points.items() if m < baseline_end]
        mean = statistics.fmean(base_values) if base_values else 0.0
        std = statistics.pstdev(base_values) if len(base_values) > 1 else 0.0
        use_z = base_name in zscore_signals and std > 0
        floor = max(std, abs(mean) * cfg["std_floor_pct_of_mean"] / 100, 1e-9)

        run: list[tuple[datetime, float, Optional[float], bool]] = []

        def close_run() -> None:
            if not run:
                return
            peak = max(run, key=lambda x: x[1])
            first_minute = run[0][0]
            ids = list(dict.fromkeys(item.records.get(first_minute, [])[:2] + item.records.get(peak[0], [])[:3]))[:5]
            found.append({"service": service, "signal": signal, "observed": peak[1], "baseline": mean,
                          "z_score": round(peak[2], 2) if peak[2] is not None else None, "detected_at": first_minute,
                          "evidence_ids": ids, "method": "threshold" if any(x[3] for x in run) else "zscore"})
            run.clear()

        for minute in sorted(item.points):
            value = item.points[minute]
            z = (value - mean) / floor if use_z else None
            by_z = z is not None and z >= cfg["zscore_threshold"]
            by_threshold = threshold is not None and OPERATORS[threshold["operator"]](value, threshold["value"])
            if by_z or by_threshold:
                if run and minute - run[-1][0] > gap:
                    close_run()
                run.append((minute, value, z, by_threshold))
        close_run()

    found.sort(key=lambda e: (e["detected_at"], e["service"], e["signal"]))
    events = [AnomalyEvent(anomaly_id=f"ANM-{i}", **{**e, "observed": round(float(e["observed"]), 4),
                                                    "baseline": round(float(e["baseline"]), 4)})
              for i, e in enumerate(found, start=1)]
    if log:
        for event in events:
            log_interaction("anomaly_detected", component="anomaly_detector", anomaly_id=event.anomaly_id,
                            service=event.service, signal=event.signal, observed=event.observed,
                            baseline=event.baseline, z_score=event.z_score, method=event.method,
                            detected_at=event.detected_at.isoformat(), evidence_ids=event.evidence_ids,
                            scenario_id=scenario_id)
    return events

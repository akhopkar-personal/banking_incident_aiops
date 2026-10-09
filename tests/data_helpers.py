"""Helpers for the Phase 2 data tests: load scenario files and compute the
severity-rule signals of Architecture Spec Section 5.3.2.

The signal code here is a test oracle for the generated data only. The real
Severity Rules Engine (src/services/severity_rules.py, Phase 3) must reach the
same results; once it exists, the scenario tests can call it as well.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from data.generators.builder import SOURCE_FILES
from src.config import DEFAULT_CUSTOMER_FACING_SERVICES, REPO_ROOT, SCENARIO_DIRS

TELEMETRY = REPO_ROOT / "data" / "telemetry"
SEVERITY_ORDER = {"S1": 1, "S2": 2, "S3": 3, "S4": 4}


def scenario_path(dataset_id: str) -> Path:
    return TELEMETRY / SCENARIO_DIRS[dataset_id]


def manifest(dataset_id: str) -> dict[str, Any]:
    return json.loads((scenario_path(dataset_id) / "manifest.json").read_text(encoding="utf-8"))


def load(dataset_id: str, src: str) -> list[dict[str, Any]]:
    """Records of one source, with CSV values parsed (empty -> absent, JSON dict columns decoded)."""
    path = scenario_path(dataset_id) / SOURCE_FILES[src]
    if path.suffix == ".csv":
        with open(path, encoding="utf-8", newline="") as handle:
            rows = []
            for raw in csv.DictReader(handle):
                row = {k: v for k, v in raw.items() if v != ""}
                if "status_code_breakdown" in row:
                    row["status_code_breakdown"] = json.loads(row["status_code_breakdown"])
                for key in ("request_count", "active_connections", "max_connections", "slow_query_count"):
                    if key in row:
                        row[key] = int(row[key])
                for key in ("error_rate", "p95_latency_ms", "cpu_pct", "mem_pct", "lock_wait_ms"):
                    if key in row:
                        row[key] = float(row[key])
                rows.append(row)
            return rows
    return json.loads(path.read_text(encoding="utf-8"))


def minute_of(record: dict[str, Any], window_start: str) -> int:
    t = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
    t0 = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
    return int((t - t0).total_seconds() // 60)


def longest_run(series: dict[int, float], predicate: Callable[[float], bool]) -> int:
    """Longest run of consecutive minutes whose value satisfies `predicate`."""
    best = run = 0
    previous = None
    for minute in sorted(series):
        if predicate(series[minute]):
            run = run + 1 if previous is not None and minute == previous + 1 and run else 1
        else:
            run = 0
        previous = minute
        best = max(best, run)
    return best


class Signals:
    """Signal values for one dataset, computed over its whole window."""

    def __init__(self, dataset_id: str):
        self.dataset_id = dataset_id
        self.start = manifest(dataset_id)["window_start"]
        self.api = load(dataset_id, "API")
        self.kafka = load(dataset_id, "KFK")
        self.quorum = load(dataset_id, "QRM")
        self.complaints = load(dataset_id, "CMP")

    def api_series(self, service: str, field: str, endpoint: str | None = None) -> dict[int, float]:
        """Per minute, the max of `field` over the service's endpoints (or one endpoint)."""
        series: dict[int, float] = {}
        for row in self.api:
            if row["service"] == service and (endpoint is None or row["endpoint"] == endpoint):
                m = minute_of(row, self.start)
                series[m] = max(series.get(m, 0.0), row[field])
        return series

    def baseline_p95(self, service: str) -> float:
        values = [v for m, v in self.api_series(service, "p95_latency_ms").items() if m < 20]
        return sum(values) / len(values)

    def p95_ratio(self, service: str) -> dict[int, float]:
        base = self.baseline_p95(service)
        return {m: v / base for m, v in self.api_series(service, "p95_latency_ms").items()}

    def kafka_series(self, field: str, **match: Any) -> dict[int, float]:
        series: dict[int, float] = {}
        for row in self.kafka:
            if row.get(field) is not None and all(row.get(k) == v for k, v in match.items()):
                m = minute_of(row, self.start)
                series[m] = max(series.get(m, 0), row[field])
        return series

    def duplicate_transaction_ids(self) -> int:
        counts = Counter(r["transaction_id"] for r in self.kafka
                         if r["event_type"] == "produced" and r["topic"] == "payments.transactions")
        return sum(1 for n in counts.values() if n > 1)

    def complaints_per_product_30min(self) -> dict[str, int]:
        by_product: dict[str, list[int]] = defaultdict(list)
        for row in self.complaints:
            by_product[row["product"]].append(minute_of(row, self.start))
        best = {}
        for product, minutes in by_product.items():
            minutes.sort()
            best[product] = max(sum(1 for x in minutes if m <= x < m + 30) for m in minutes)
        return best

    def fired_pass1(self) -> set[str]:
        """Pass-1 rules (Req. Section 10.6) that fire on this dataset."""
        cf = DEFAULT_CUSTOMER_FACING_SERVICES
        fired = set()
        if any(longest_run(self.api_series(s, "error_rate"), lambda v: v * 100 > 15) >= 3 for s in cf):
            fired.add("R-S1-ERR")
        routes = {r["endpoint"] for r in self.api if r["service"] == "payment-network-gateway"}
        if any(longest_run(self.api_series("payment-network-gateway", "error_rate", e), lambda v: v * 100 >= 50) >= 3
               for e in routes):
            fired.add("R-S1-ROUTE")
        if sum(1 for s in cf if longest_run(self.p95_ratio(s), lambda v: v > 3) >= 5) >= 2:
            fired.add("R-S1-LAT")
        if max(r["offline_partitions"] for r in self.quorum) > 0:
            fired.add("R-S1-OFFLINE")
        urp = self.kafka_series("under_replicated", service="kafka-platform", event_type="metric_sample")
        if longest_run(urp, lambda v: v > 0) >= 5:
            fired.add("R-S2-URP")
        lag = self.kafka_series("lag", topic="payments.transactions", event_type="metric_sample")
        if longest_run(lag, lambda v: v > 10_000) >= 5:
            fired.add("R-S2-LAG")
        if self.duplicate_transaction_ids() > 0:
            fired.add("R-S2-DUP")
        if max(self.complaints_per_product_30min().values()) >= 50:
            fired.add("R-S2-CMP")
        for s in cf:
            errors, ratio = self.api_series(s, "error_rate"), self.p95_ratio(s)
            degraded = {m: float(errors[m] > 0.02 or ratio[m] > 2) for m in errors}
            if longest_run(degraded, lambda v: v > 0) >= 5:
                fired.add("R-S3-DEG")
                break
        return fired


def rule_severities() -> dict[str, str]:
    rules = yaml.safe_load((REPO_ROOT / "data" / "severity_rules.yaml").read_text(encoding="utf-8"))["rules"]
    return {r["id"]: r.get("severity") or r.get("min_severity") for r in rules}


def highest(severities: set[str]) -> str:
    return min(severities, key=SEVERITY_ORDER.__getitem__) if severities else "S4"

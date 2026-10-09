"""query_db_infra_metrics (Architecture Spec Section 7.3): connection use,
CPU, slow queries and lock waits."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld


def query_db_infra_metrics(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                           db_instance: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_db_infra_metrics", ctx, "DBM", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "DBM", window_start, window_end, service,
                  lambda d: db_instance is None or d["db_instance"] == db_instance)
    if not rows:
        return summary("query_db_infra_metrics", ctx, "DBM", service, window_start, window_end, 0, {})

    def used(row) -> float:
        return row.data["active_connections"] / row.data["max_connections"]

    base_rows = [r for r in rows if r.ts < rows[0].ts + timedelta(minutes=20)]
    base_lock = sum(r.data["lock_wait_ms"] for r in base_rows) / len(base_rows)
    peak_used = max(rows, key=used)
    peak_cpu = max(rows, key=lambda r: r.data["cpu_pct"])
    peak_lock = max(rows, key=lambda r: r.data["lock_wait_ms"])
    peak_slow = max(rows, key=lambda r: r.data["slow_query_count"])
    aggregates = {
        "max_connections_used_pct": used(peak_used) * 100,
        "max_cpu_pct": peak_cpu.data["cpu_pct"],
        "max_slow_queries": peak_slow.data["slow_query_count"],
        "max_lock_wait_ms": peak_lock.data["lock_wait_ms"],
        "baseline_lock_wait_ms": round(base_lock, 1),
        "minutes_connections_over_90pct": sum(1 for r in rows if used(r) > 0.9),
    }
    candidates = []
    saturated = [r for r in rows if used(r) > 0.9]
    if saturated:
        candidates.append((9, evidence("DBM", saturated[0], f"{saturated[0].data['db_instance']} connections first "
                                                            f"above 90%: {used(saturated[0]):.0%} of max")))
    if used(peak_used) > 0.8:
        candidates.append((8, evidence("DBM", peak_used, f"Peak connections {peak_used.data['active_connections']}/"
                                                         f"{peak_used.data['max_connections']} ({used(peak_used):.0%})")))
    if peak_cpu.data["cpu_pct"] > 85:
        candidates.append((7, evidence("DBM", peak_cpu, f"Peak CPU {peak_cpu.data['cpu_pct']:.1f}%")))
    if peak_lock.data["lock_wait_ms"] > 3 * base_lock:
        candidates.append((7, evidence("DBM", peak_lock, f"Peak lock wait {peak_lock.data['lock_wait_ms']:.0f} ms "
                                                         f"({peak_lock.data['lock_wait_ms'] / base_lock:.0f}x baseline)")))
    if peak_slow.data["slow_query_count"] > 10:
        candidates.append((6, evidence("DBM", peak_slow, f"Peak slow queries {peak_slow.data['slow_query_count']}/min")))
    return summary("query_db_infra_metrics", ctx, "DBM", service, window_start, window_end, len(rows), aggregates,
                   candidates)

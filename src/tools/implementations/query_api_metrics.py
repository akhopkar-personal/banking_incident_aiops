"""query_api_metrics (Architecture Spec Section 7.3): per service and endpoint,
max error rate, max p95 and baseline p95 (mean of the first 20 minutes of
data in the window, as the anomaly detector uses)."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld

BASELINE_MINUTES = 20


def query_api_metrics(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                      endpoint: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_api_metrics", ctx, "API", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "API", window_start, window_end, service,
                  lambda d: endpoint is None or d["endpoint"] == endpoint)
    series: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        series[(row.service, row.data["endpoint"])].append(row)

    aggregates: dict[str, float] = {}
    candidates = []
    for (svc, ep), members in sorted(series.items()):
        start = members[0].ts
        base = [r.data["p95_latency_ms"] for r in members if r.ts < start + timedelta(minutes=BASELINE_MINUTES)]
        baseline = sum(base) / len(base)
        worst_err = max(members, key=lambda r: r.data["error_rate"])
        worst_p95 = max(members, key=lambda r: r.data["p95_latency_ms"])
        key = f"{svc}{ep}"
        aggregates[f"{key}.max_error_rate"] = worst_err.data["error_rate"]
        aggregates[f"{key}.max_p95_ms"] = worst_p95.data["p95_latency_ms"]
        aggregates[f"{key}.baseline_p95_ms"] = round(baseline, 1)
        degraded = [r for r in members if r.data["error_rate"] > 0.02 or r.data["p95_latency_ms"] > 2 * baseline]
        aggregates[f"{key}.degraded_minutes"] = len(degraded)
        if worst_err.data["error_rate"] > 0.02:
            first = next(r for r in members if r.data["error_rate"] > 0.02)
            candidates.append((worst_err.data["error_rate"] * 100, evidence(
                "API", worst_err, f"{svc} {ep} error rate {worst_err.data['error_rate']:.1%} at {worst_err.ts:%H:%M}Z "
                                  f"(above 2% since {first.ts:%H:%M}Z, {len(degraded)} degraded minutes)")))
            candidates.append((worst_err.data["error_rate"] * 100 - 1, evidence(
                "API", first, f"{svc} {ep} error rate first above 2%: {first.data['error_rate']:.1%}")))
        ratio = worst_p95.data["p95_latency_ms"] / baseline
        if ratio > 2:
            candidates.append((ratio * 10, evidence(
                "API", worst_p95, f"{svc} {ep} p95 {worst_p95.data['p95_latency_ms']:.0f} ms = {ratio:.1f}x baseline "
                                  f"{baseline:.0f} ms at {worst_p95.ts:%H:%M}Z")))
    return summary("query_api_metrics", ctx, "API", service, window_start, window_end, len(rows), aggregates,
                   candidates)

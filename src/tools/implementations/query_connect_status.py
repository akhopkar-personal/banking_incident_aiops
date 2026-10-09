"""query_connect_status (Architecture Spec Section 7.3): Kafka Connect task states."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld


def query_connect_status(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                         connector: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_connect_status", ctx, "CON", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "CON", window_start, window_end, service,
                  lambda d: connector is None or d["connector"] == connector)
    aggregates: dict[str, float] = {"total": len(rows)}
    aggregates.update({f"state.{k}": v for k, v in Counter(r.data["state"] for r in rows).items()})
    not_running = [r for r in rows if r.data["state"] != "RUNNING"]
    aggregates["failed_tasks"] = len({(r.data["connector"], r.data["task_id"]) for r in not_running
                                      if r.data["state"] == "FAILED"})
    candidates = [(9 if r.data["state"] == "FAILED" else 5, evidence(
        "CON", r, f"Connector {r.data['connector']} task {r.data['task_id']} {r.data['state']}"
                  f"{': ' + r.data['trace_excerpt'] if r.data.get('trace_excerpt') else ''}")) for r in not_running]
    return summary("query_connect_status", ctx, "CON", service, window_start, window_end, len(rows), aggregates,
                   candidates)

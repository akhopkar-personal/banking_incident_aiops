"""query_change_records (Architecture Spec Section 7.3): all changes from
`lookback_min` before the window start to its end, every one returned as
notable (max 20)."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, parse_time, select, summary, withheld


def query_change_records(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                         lookback_min: int = 180, change_type: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_change_records", ctx, "DEP", service, window_start, window_end)) is not None:
        return empty
    start = parse_time(window_start) - timedelta(minutes=lookback_min)
    rows = select(ctx, "DEP", start, window_end, service,
                  lambda d: change_type is None or d["change_type"] == change_type)
    aggregates: dict[str, float] = {"total": len(rows), "lookback_min": lookback_min}
    aggregates.update({f"change_type.{k}": v for k, v in Counter(r.data["change_type"] for r in rows).items()})
    # Most recent first: a change shortly before onset is the usual suspect.
    candidates = [(r.ts.timestamp(), evidence(
        "DEP", r, f"{r.data['change_type']} on {r.service} at {r.ts:%Y-%m-%d %H:%M}Z: {r.data['change_summary']}"
                  f"{' (rollback available)' if r.data['rollback_available'] else ''}")) for r in rows]
    return summary("query_change_records", ctx, "DEP", service, start, window_end, len(rows), aggregates, candidates)

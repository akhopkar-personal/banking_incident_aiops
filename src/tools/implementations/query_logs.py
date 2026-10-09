"""query_logs (Architecture Spec Section 7.3): counts by level and error code,
and the most frequent messages, ERROR before WARN before INFO."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld

LEVEL_PRIORITY = {"ERROR": 3, "WARN": 2, "WARNING": 2, "INFO": 1, "DEBUG": 0}


def query_logs(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
               level: Optional[str] = None, error_code: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_logs", ctx, "LOG", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "LOG", window_start, window_end, service,
                  lambda d: (level is None or d["level"].upper() == level.upper())
                  and (error_code is None or d.get("error_code") == error_code))
    by_level = Counter(r.data["level"].upper() for r in rows)
    by_code = Counter(r.data["error_code"] for r in rows if r.data.get("error_code"))
    aggregates: dict[str, float] = {"total": len(rows)}
    aggregates.update({f"level.{k}": v for k, v in by_level.items()})
    aggregates.update({f"error_code.{k}": v for k, v in by_code.items()})

    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        d = row.data
        groups[(d["service"], d["level"].upper(), d.get("error_code"), d["message"])].append(row)
    candidates = []
    for (svc, lvl, code, message), members in groups.items():
        first = members[0]
        text = (f"{lvl} {svc}{' ' + code if code else ''}: {message} "
                f"(x{len(members)}, first {first.ts:%H:%M:%S}Z)")
        candidates.append((LEVEL_PRIORITY.get(lvl, 0) * 1e6 + len(members), evidence("LOG", first, text)))
    return summary("query_logs", ctx, "LOG", service, window_start, window_end, len(rows), aggregates, candidates)

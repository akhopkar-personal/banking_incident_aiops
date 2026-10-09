"""query_schema_registry (Architecture Spec Section 7.3): compatibility errors by subject."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld


def query_schema_registry(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                          subject: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_schema_registry", ctx, "SRG", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "SRG", window_start, window_end, service, lambda d: subject is None or d["subject"] == subject)
    errors = [r for r in rows if r.data.get("error")]
    aggregates: dict[str, float] = {"total": len(rows), "errors": len(errors), "subjects": len({r.data["subject"] for r in rows})}
    aggregates.update({f"errors.{k}": v for k, v in Counter(r.data["subject"] for r in errors).items()})
    candidates = [(8, evidence("SRG", r, f"{r.data['subject']} v{r.data['version']} ({r.data['compatibility']}): "
                                         f"{r.data['error']}")) for r in errors]
    return summary("query_schema_registry", ctx, "SRG", service, window_start, window_end, len(rows), aggregates,
                   candidates)

"""query_acl_audit (Architecture Spec Section 7.3): denied operations by
resource and the change records they refer to."""

from __future__ import annotations

from collections import Counter
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld


def query_acl_audit(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                    resource: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_acl_audit", ctx, "ACL", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "ACL", window_start, window_end, service, lambda d: resource is None or d["resource"] == resource)
    denied = [r for r in rows if r.data["result"] == "DENIED"]
    aggregates: dict[str, float] = {"total": len(rows), "denied": len(denied),
                                    "related_change_refs": len({r.data["change_ref"] for r in rows
                                                                if r.data.get("change_ref")})}
    aggregates.update({f"denied.{k}": v for k, v in Counter(r.data["resource"] for r in denied).items()})
    candidates = [(9, evidence("ACL", r, f"DENIED {r.data['operation']} on {r.data['resource']} for "
                                         f"{r.data['principal']}"
                                         f"{' (change ' + r.data['change_ref'] + ')' if r.data.get('change_ref') else ''}"))
                  for r in denied]
    return summary("query_acl_audit", ctx, "ACL", service, window_start, window_end, len(rows), aggregates, candidates)

"""query_cluster_quorum (Architecture Spec Section 7.3): controller quorum
health, session expirations, offline partitions and controller changes."""

from __future__ import annotations

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld


def query_cluster_quorum(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg
                         ) -> ToolSummary:
    if (empty := withheld("query_cluster_quorum", ctx, "QRM", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "QRM", window_start, window_end, service)
    unhealthy = [r for r in rows if not r.data["quorum_healthy"]]
    expired = [r for r in rows if r.data["session_expirations"] > 0]
    offline = [r for r in rows if r.data["offline_partitions"] > 0]
    changes = [b for a, b in zip(rows, rows[1:]) if a.data["controller_id"] != b.data["controller_id"]]
    aggregates = {
        "total": len(rows),
        "quorum_unhealthy_samples": len(unhealthy),
        "max_session_expirations": max((r.data["session_expirations"] for r in rows), default=0),
        "session_expiration_samples": len(expired),
        "max_offline_partitions": max((r.data["offline_partitions"] for r in rows), default=0),
        "controller_changes": len(changes),
    }
    candidates = []
    if offline:
        candidates.append((10, evidence("QRM", offline[0], f"{offline[0].data['offline_partitions']} offline partitions")))
    if unhealthy:
        candidates.append((9, evidence("QRM", unhealthy[0], "Controller quorum unhealthy")))
    if expired:
        candidates.append((6, evidence("QRM", expired[0], f"Broker session expirations: "
                                                          f"{expired[0].data['session_expirations']} "
                                                          f"({len(expired)} samples)")))
    for row in changes:
        candidates.append((5, evidence("QRM", row, f"Controller moved to {row.data['controller_id']}")))
    return summary("query_cluster_quorum", ctx, "QRM", service, window_start, window_end, len(rows), aggregates,
                   candidates)

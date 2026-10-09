"""query_network (Architecture Spec Section 7.3): TLS handshake failures by
route, latency and certificate expiry."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, parse_time, select, summary, withheld


def query_network(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                  destination: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_network", ctx, "NET", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "NET", window_start, window_end, service,
                  lambda d: destination is None or d["destination"] == destination)
    by_route: dict[str, list] = defaultdict(list)
    for row in rows:
        by_route[row.data.get("route") or row.data["destination"]].append(row)
    end = parse_time(window_end)
    aggregates: dict[str, float] = {}
    candidates = []
    for route, members in sorted(by_route.items()):
        failing = [r for r in members if r.data["tls_status"] != "ok"]
        aggregates[f"{route}.handshake_failures"] = len(failing)
        aggregates[f"{route}.max_latency_ms"] = max(r.data["latency_ms"] for r in members)
        aggregates[f"{route}.max_packet_loss_pct"] = max(r.data["packet_loss_pct"] for r in members)
        expiries = [r for r in members if r.data.get("cert_expiry")]
        if expiries:
            soonest = min(expiries, key=lambda r: r.data["cert_expiry"])
            days = (parse_time(soonest.data["cert_expiry"]) - end).total_seconds() / 86400
            aggregates[f"{route}.cert_expiry_days_after_window"] = round(days, 2)
            if days < 30:
                candidates.append((8, evidence("NET", soonest, f"Route {route} certificate expires "
                                                               f"{soonest.data['cert_expiry']}")))
        if failing:
            first = failing[0]
            candidates.append((10, evidence("NET", first, f"Route {route}: TLS {first.data['tls_status']} from "
                                                          f"{first.ts:%H:%M}Z ({len(failing)} of {len(members)} samples)")))
    return summary("query_network", ctx, "NET", service, window_start, window_end, len(rows), aggregates, candidates)

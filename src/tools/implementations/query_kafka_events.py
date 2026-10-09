"""query_kafka_events (Architecture Spec Section 7.3): lag, rebalances, broker
failures, under-replicated partitions and duplicate transaction IDs."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

from src.schemas.evidence import ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld


def query_kafka_events(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                       topic: Optional[str] = None, event_type: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_kafka_events", ctx, "KFK", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "KFK", window_start, window_end, service,
                  lambda d: (topic is None or d["topic"] == topic) and (event_type is None or d["event_type"] == event_type))
    candidates = []
    aggregates: dict[str, float] = {"total": len(rows)}

    lag_rows = [r for r in rows if r.data.get("lag") is not None]
    peak_by_group: dict[str, object] = {}
    for row in lag_rows:
        group = row.data.get("consumer_group", "?")
        if group not in peak_by_group or row.data["lag"] > peak_by_group[group].data["lag"]:
            peak_by_group[group] = row
    aggregates["max_lag"] = max((r.data["lag"] for r in lag_rows), default=0)
    for group, row in peak_by_group.items():
        aggregates[f"max_lag.{group}"] = row.data["lag"]
        if row.data["lag"] > 10_000:
            candidates.append((5, evidence("KFK", row, f"Peak consumer lag {row.data['lag']:,} for {group} on "
                                                        f"{row.data['topic']} at {row.ts:%H:%M}Z")))

    rebalances = [r for r in rows if r.data["event_type"] == "rebalance"]
    aggregates["rebalances"] = len(rebalances)
    first_last: dict[str, list] = defaultdict(list)
    for row in rebalances:
        first_last[row.data.get("consumer_group", "?")].append(row)
    for group, members in first_last.items():
        for row in (members[0], members[-1]):
            candidates.append((4, evidence("KFK", row, f"Rebalance of {group} ({len(members)} in window): "
                                                        f"{row.data.get('error', '')}")))

    down = [r for r in rows if r.data["event_type"] == "broker_down"]
    aggregates["broker_down_events"] = len(down)
    for row in down:
        candidates.append((9, evidence("KFK", row, f"Broker down at {row.ts:%H:%M:%S}Z: {row.data.get('error', '')}")))

    urp = [r for r in rows if r.data.get("under_replicated") is not None]
    aggregates["max_under_replicated"] = max((r.data["under_replicated"] for r in urp), default=0)
    urp_on = [r for r in urp if r.data["under_replicated"] > 0]
    aggregates["under_replicated_minutes"] = len({r.ts.replace(second=0) for r in urp_on})
    if urp_on:
        peak = max(urp_on, key=lambda r: (r.data["under_replicated"], -r.ts.timestamp()))
        candidates.append((7, evidence("KFK", urp_on[0], f"Under-replicated partitions first seen: "
                                                          f"{urp_on[0].data['under_replicated']} (ISR "
                                                          f"{urp_on[0].data.get('isr_count')})")))
        candidates.append((6, evidence("KFK", peak, f"Peak under-replicated partitions: {peak.data['under_replicated']}")))

    produced = [r for r in rows if r.data["event_type"] == "produced" and r.data.get("transaction_id")]
    counts = Counter(r.data["transaction_id"] for r in produced)
    duplicates = {t for t, n in counts.items() if n > 1}
    aggregates["produced"] = len(produced)
    aggregates["duplicate_transaction_ids"] = len(duplicates)
    seen: set[str] = set()
    repeats = []
    for row in produced:
        txn = row.data["transaction_id"]
        if txn in duplicates:
            if txn in seen:
                repeats.append(row)
            seen.add(txn)
    for row in repeats[:5]:
        candidates.append((8, evidence("KFK", row, f"Transaction {row.data['transaction_id']} produced again on "
                                                    f"{row.data['topic']} ({len(duplicates)} duplicated IDs in window)")))
    return summary("query_kafka_events", ctx, "KFK", service, window_start, window_end, len(rows), aggregates,
                   candidates)

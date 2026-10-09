"""query_complaints (Architecture Spec Section 7.3): complaint clusters by
product and keyword topic, volume over time, sentiment from a small lexicon,
estimated impact and the earliest signal. No LLM call."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Optional

from src.schemas.evidence import ComplaintAnalysis, ComplaintCluster, ToolSummary

from ._common import ServiceArg, TimeArg, ToolContext, evidence, select, summary, withheld

# Checked in order; the first topic whose keywords match wins.
TOPICS: list[tuple[str, tuple[str, ...]]] = [
    # About being charged: "my transfer failed twice" is a failure, not a double charge.
    ("double_charge", ("charged twice", "charged two times", "double charge", "charged again", "same payment",
                       "appears two times", "debited twice")),
    ("card_declined", ("declined",)),
    ("missing_alert", ("alert", "sms", "notification")),
    ("stale_balance", ("balance",)),
    ("slow_login", ("slow", "loading", "loads forever", "spinning", "log in", "login")),
    ("payment_failed", ("fail", "failed", "error", "stuck", "cannot", "can't", "times out")),
]
NEGATIVE = {"failed", "fail", "frustrating", "unacceptable", "not", "wrong", "worried", "urgently", "declined",
            "missing", "twice", "slow", "never", "stuck", "error", "cannot", "charged", "double", "forever"}
POSITIVE = {"thanks", "thank", "resolved", "working", "great", "quick", "fine"}


def topic_of(text: str) -> str:
    lower = text.lower()
    for topic, keywords in TOPICS:
        if any(k in lower for k in keywords):
            return topic
    return "other"


def sentiment(text: str) -> float:
    words = re.findall(r"[a-z']+", text.lower())
    neg = sum(1 for w in words if w in NEGATIVE)
    pos = sum(1 for w in words if w in POSITIVE)
    if not neg and not pos:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


def query_complaints(ctx: ToolContext, service: ServiceArg, window_start: TimeArg, window_end: TimeArg,
                     product: Optional[str] = None) -> ToolSummary:
    if (empty := withheld("query_complaints", ctx, "CMP", service, window_start, window_end)) is not None:
        return empty
    rows = select(ctx, "CMP", window_start, window_end, service, lambda d: product is None or d["product"] == product)
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for row in rows:
        groups[(row.data["product"], topic_of(row.data["text"]))].append(row)

    clusters = []
    for (prod, topic), members in groups.items():
        clusters.append(ComplaintCluster(
            cluster_id=f"CL-{prod}-{topic}", topic=topic, product=prod,
            complaint_ids=[m.data["complaint_id"] for m in members][:50], first_seen=members[0].ts,
            volume=len(members), sentiment_score=round(sum(sentiment(m.data["text"]) for m in members) / len(members), 3),
        ))
    clusters.sort(key=lambda c: (-c.volume, c.first_seen))

    def max_in_30(members) -> int:
        times = [m.ts for m in members]
        return max((sum(1 for t in times if s <= t < s + timedelta(minutes=30)) for s in times), default=0)

    by_product: dict[str, list] = defaultdict(list)
    for row in rows:
        by_product[row.data["product"]].append(row)
    aggregates: dict[str, float] = {"total": len(rows), "clusters": len(clusters)}
    for prod, members in by_product.items():
        aggregates[f"product.{prod}"] = len(members)
        aggregates[f"max_30min.{prod}"] = max_in_30(members)

    top = clusters[0] if clusters else None
    significant = [c for c in clusters if c.volume >= 10]
    earliest = min((c.first_seen for c in significant), default=top.first_seen if top else None)
    impact = (f"{len(rows)} complaints in the window. Largest cluster: {top.volume} about {top.topic.replace('_', ' ')} "
              f"on {top.product}, first at {top.first_seen:%H:%M}Z." if top else "No complaints in the window.")
    analysis = ComplaintAnalysis(clusters=clusters, total_complaints_in_window=len(rows),
                                 estimated_customer_impact=impact, earliest_signal_time=earliest)

    candidates = []
    for rank, cluster in enumerate(clusters[:6]):
        members = groups[(cluster.product, cluster.topic)]
        for member in members[:2]:
            candidates.append((cluster.volume - rank * 0.1, evidence(
                "CMP", member, f"Complaint ({cluster.product}, {cluster.topic}, cluster of {cluster.volume}): "
                               f"{member.data['text']}")))
    linked = [r for r in rows if r.data.get("transaction_id")]
    for row in linked[:3]:
        candidates.append((1000, evidence("CMP", row, f"Complaint naming transaction {row.data['transaction_id']}: "
                                                      f"{row.data['text']}")))
    return summary("query_complaints", ctx, "CMP", service, window_start, window_end, len(rows), aggregates,
                   candidates, complaint_analysis=analysis)

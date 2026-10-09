"""Alert Correlation and Dedup Service (Architecture Spec Section 5.2; Req. FR-38).

Groups anomaly events into incidents and turns each group into exactly one
incident, keyed by an idempotency key, so an alert storm or a replay never
creates a second incident, ticket or page.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.logger_setup import log_interaction
from src.schemas.enums import IncidentState
from src.schemas.incident import AnomalyEvent
from src.services import incident_state_store as store
from src.tools.implementations.get_service_dependencies import downstream_of, load_dependency_map, upstream_of

BUCKET = timedelta(minutes=30)
_lock = threading.Lock()


@dataclass
class IncidentGroup:
    root_service: str
    events: list[AnomalyEvent]
    bucket_start: datetime
    idempotency_key: str
    services: list[str] = field(default_factory=list)


def idempotency_key(root_service: str, bucket_start: datetime) -> str:
    return hashlib.sha256(f"{root_service}|{bucket_start.isoformat()}".encode()).hexdigest()[:16]


def connected(a: str, b: str, deps: dict) -> bool:
    """Same service, or one depends on the other (directly or transitively)."""
    return a == b or (a in deps and b in upstream_of(a, deps)) or (b in deps and a in upstream_of(b, deps))


def _is_complaint(event: AnomalyEvent) -> bool:
    return event.signal.startswith("complaints_30min")


def _root(events: list[AnomalyEvent], deps: dict) -> str:
    """The earliest anomalous service that is upstream of another anomalous service in the group;
    ties go to the one with more anomalous services downstream. If none, the earliest service."""
    first_seen: dict[str, datetime] = {}
    for e in events:
        first_seen[e.service] = min(first_seen.get(e.service, e.detected_at), e.detected_at)
    services = set(first_seen)
    affected_below = {s: len(set(downstream_of(s, deps)) & services) if s in deps else 0 for s in services}
    candidates = [s for s in services if affected_below[s] > 0]
    pool = candidates or list(services)
    return min(pool, key=lambda s: (first_seen[s], -affected_below[s], s))


def group(events: list[AnomalyEvent], deps: Optional[dict] = None) -> list[IncidentGroup]:
    """Section 5.2: same 30-minute bucket from the group's first event and connected services
    form one group. A series that keeps going after the bucket stays in its group. Complaint
    volume events join the group whose bucket they fall in (or the largest group), since
    complaints are filed against products rather than the failing component."""
    deps = deps or load_dependency_map()
    groups: list[dict] = []
    ordered = sorted(events, key=lambda e: (e.detected_at, e.anomaly_id))
    for event in [e for e in ordered if not _is_complaint(e)]:
        matches = [g for g in groups
                   if (event.service, event.signal) in g["series"]
                   or (event.detected_at < g["start"] + BUCKET and any(connected(event.service, s, deps)
                                                                        for s in g["services"]))]
        if not matches:
            groups.append({"start": event.detected_at, "events": [event], "services": {event.service},
                           "series": {(event.service, event.signal)}})
            continue
        target = matches[0]
        for other in matches[1:]:  # the event connects groups: merge them
            target["events"] += other["events"]
            target["services"] |= other["services"]
            target["series"] |= other["series"]
            target["start"] = min(target["start"], other["start"])
            groups.remove(other)
        target["events"].append(event)
        target["services"].add(event.service)
        target["series"].add((event.service, event.signal))
    for event in [e for e in ordered if _is_complaint(e)]:
        in_bucket = [g for g in groups if g["start"] <= event.detected_at < g["start"] + BUCKET]
        connected_groups = [g for g in in_bucket if any(connected(event.service, s, deps) for s in g["services"])]
        target = (connected_groups or in_bucket or [None])[0]
        if target is None:
            groups.append({"start": event.detected_at, "events": [event], "services": {event.service},
                           "series": {(event.service, event.signal)}})
        else:
            target["events"].append(event)

    out = []
    for g in groups:
        g["events"].sort(key=lambda e: (e.detected_at, e.anomaly_id))
        core = [e for e in g["events"] if not _is_complaint(e)] or g["events"]
        root = _root(core, deps)
        out.append(IncidentGroup(root_service=root, events=g["events"], bucket_start=g["start"],
                                 idempotency_key=idempotency_key(root, g["start"]),
                                 services=sorted({e.service for e in g["events"]})))
    out.sort(key=lambda g: (g.bucket_start, -len(g.events)))
    return out


def primary_group(groups: list[IncidentGroup]) -> Optional[IncidentGroup]:
    """The group a replay investigates: the one with the most anomaly events (earliest on a tie)."""
    return max(groups, key=lambda g: (len(g.events), -g.bucket_start.timestamp())) if groups else None


def upsert_incident(incident_group: IncidentGroup, *, reference_time: datetime, run_id: str,
                    scenario_id: Optional[str] = None, actor: str = "correlate_dedup") -> tuple[str, bool]:
    """(incident_id, created). An existing incident with the same key is reused and the
    repeat is logged as incident_deduplicated: no second incident, ticket or page."""
    with _lock:
        existing = store.find_by_key(incident_group.idempotency_key)
        if existing:
            log_interaction("incident_deduplicated", component="alert_correlator", incident_id=existing,
                            run_id=run_id, idempotency_key=incident_group.idempotency_key,
                            anomaly_count=len(incident_group.events), root_service=incident_group.root_service)
            return existing, False
        incident_id = store.next_incident_id(reference_time)
        store.append(incident_id, run_id, "created", IncidentState.OPEN, actor, {
            "idempotency_key": incident_group.idempotency_key, "root_service": incident_group.root_service,
            "bucket_start": incident_group.bucket_start.isoformat(), "scenario_id": scenario_id,
            "services": incident_group.services, "anomaly_ids": [e.anomaly_id for e in incident_group.events],
        })
        log_interaction("incident_created", component="alert_correlator", incident_id=incident_id, run_id=run_id,
                        idempotency_key=incident_group.idempotency_key, root_service=incident_group.root_service,
                        anomaly_count=len(incident_group.events), scenario_id=scenario_id)
        return incident_id, True

"""Healthy telemetry for every source across the whole window, with background
noise (Architecture Spec Section 16.1). Scenarios then change these records or
add to them."""

from __future__ import annotations

from datetime import timedelta

from . import vocab
from .builder import ScenarioBuilder

DEFAULT_CERT_EXPIRY_DAYS = 120


def add_healthy_baseline(b: ScenarioBuilder) -> None:
    _api_metrics(b)
    _db_metrics(b)
    _kafka(b)
    _cluster_quorum(b)
    _connect(b)
    _acl(b)
    _schema_registry(b)
    _network(b)
    _log_noise(b)
    _complaint_noise(b)


def _api_metrics(b: ScenarioBuilder) -> None:
    for minute in range(b.minutes):
        for (service, endpoint), (rpm, p95, error_rate) in vocab.API_PROFILES.items():
            b.add("API", minute, service, endpoint=endpoint,
                  request_count=round(b.jitter(rpm)),
                  error_rate=round(b.jitter(error_rate, 0.25), 4),
                  p95_latency_ms=round(b.jitter(p95), 1),
                  _base_p95=p95, _base_error=error_rate)


def _db_metrics(b: ScenarioBuilder) -> None:
    p = vocab.DB_PROFILE
    for minute in range(b.minutes):
        b.add("DBM", minute, "core-banking-db", db_instance=vocab.DB_INSTANCE,
              active_connections=round(b.jitter(p["active_connections"])),
              max_connections=p["max_connections"],
              cpu_pct=round(b.jitter(p["cpu_pct"]), 1),
              mem_pct=round(b.jitter(p["mem_pct"], 0.02), 1),
              slow_query_count=b.rng.randint(0, 4),
              lock_wait_ms=round(b.jitter(p["lock_wait_ms"], 0.2), 1))


def _kafka(b: ScenarioBuilder) -> None:
    for minute in range(b.minutes):
        # Topic-level broker metrics; partition -1 means "all partitions of the topic".
        b.add("KFK", minute, "kafka-platform", topic=vocab.PAYMENTS_TOPIC, partition=-1,
              event_type="metric_sample", isr_count=3, under_replicated=0)
        for service, group in vocab.CONSUMER_GROUPS.items():
            b.add("KFK", minute, service, topic=vocab.PAYMENTS_TOPIC, partition=-1, consumer_group=group,
                  event_type="metric_sample", lag=b.rng.randint(40, 400))
        for _ in range(5):
            b.add("KFK", b.event_minute(minute), "payments-service", topic=vocab.PAYMENTS_TOPIC,
                  partition=b.rng.randrange(vocab.TOPIC_PARTITIONS), event_type="produced",
                  transaction_id=b.next_txn())


def _cluster_quorum(b: ScenarioBuilder) -> None:
    for minute in range(b.minutes):
        b.add("QRM", minute, "kafka-platform", mode="kraft", controller_id=1, quorum_healthy=True,
              session_expirations=0, offline_partitions=0)


def _connect(b: ScenarioBuilder) -> None:
    for minute in range(0, b.minutes, 5):
        for connector, tasks in vocab.CONNECTORS:
            for task_id in range(tasks):
                b.add("CON", minute, "kafka-platform", connector=connector, task_id=task_id, state="RUNNING")


def _acl(b: ScenarioBuilder) -> None:
    for minute in range(0, b.minutes, 10):
        for principal, resource, operation in vocab.ACL_ENTRIES:
            b.add("ACL", b.event_minute(minute), "kafka-platform", principal=principal, resource=resource,
                  operation=operation, result="ALLOWED")


def _schema_registry(b: ScenarioBuilder) -> None:
    for minute in range(0, b.minutes, 15):
        for subject, version in vocab.SCHEMA_SUBJECTS:
            b.add("SRG", minute, "kafka-platform", subject=subject, version=version, compatibility="BACKWARD")


def _network(b: ScenarioBuilder) -> None:
    default_expiry = b.start + timedelta(days=DEFAULT_CERT_EXPIRY_DAYS)
    for minute in range(b.minutes):
        for (source, destination, route), latency in vocab.NETWORK_ROUTES.items():
            b.add("NET", minute, source, source=source, destination=destination, route=route,
                  latency_ms=round(b.jitter(latency, 0.1), 1),
                  packet_loss_pct=round(b.rng.uniform(0.0, 0.2), 2),
                  tls_status="ok", cert_expiry=default_expiry)


def _log_noise(b: ScenarioBuilder) -> None:
    for minute in range(b.minutes):
        for service, level, message, code in b.rng.sample(vocab.NOISE_LOGS, 2):
            b.add("LOG", b.event_minute(minute), service, level=level, message=message, error_code=code,
                  host=b.rng.choice(vocab.HOSTS[service]))
        service, message = b.rng.choice(vocab.NOISE_INFO)
        b.add("LOG", b.event_minute(minute), service, level="INFO", message=message,
              host=b.rng.choice(vocab.HOSTS[service]))


def _complaint_noise(b: ScenarioBuilder) -> None:
    for minute in range(b.minutes):
        if b.rng.random() < 0.12:
            add_complaint(b, minute, b.rng.choice(vocab.PRODUCTS))


def add_complaint(b: ScenarioBuilder, minute: int, product: str, *, text: str | None = None,
                  transaction_id: str | None = None, tag: str | None = None) -> dict:
    """One complaint; about one in six carries synthetic personal data for redaction to remove."""
    body = text or b.rng.choice(vocab.COMPLAINT_TEXTS[product])
    pii_roll = b.rng.random()
    if pii_roll < 0.08:
        body += f" My name is {b.rng.choice(vocab.SYNTHETIC_NAMES)}, call me on {vocab.synthetic_phone(b.rng)}."
    elif pii_roll < 0.16:
        body += f" Account number {vocab.synthetic_account(b.rng)}."
    return b.add("CMP", b.event_minute(minute), _PRODUCT_SERVICE[product],
                 tag=tag, transaction_id=transaction_id, complaint_id=b.next_complaint_id(),
                 channel=b.rng.choice(vocab.CHANNELS), text=body, product=product,
                 customer_ref=f"CUST-{b.rng.randint(100000, 999999)}")


# The service that owns the customer-facing product a complaint is about.
_PRODUCT_SERVICE = {
    "fund_transfer": "payments-service",
    "card_payment": "payments-service",
    "bill_payment": "payments-service",
    "balance_and_alerts": "accounts-service",
    "account_statement": "accounts-service",
    "login": "auth-service",
}

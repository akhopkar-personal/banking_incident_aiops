"""The five golden scenarios (Req. Section 9.4; Architecture Spec Section 16.2).

Each scenario starts from the healthy baseline and adds its incident. The
required signal values from Architecture Spec Section 16.2 are produced here;
tests/test_data.py checks them. Ground truth is a draft for SME validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from . import vocab
from .baseline import add_complaint
from .builder import ScenarioBuilder


@dataclass(frozen=True)
class ScenarioDef:
    scenario_id: str
    title: str
    start: datetime
    seed: int
    onset_minute: int
    reference_minute: int  # when the alert fired or the report came in
    build: Callable[[ScenarioBuilder], None]
    issue_class: str
    expected_severity: str
    root_cause: str
    runbook_id: str
    expected_doc_ids: list[str]
    causal_change_tag: Optional[str]
    must_cite_tags: list[str]
    red_herring_tags: list[str]
    expected_rules_pass1: list[str]
    replay_mode: str  # input mode of the "replay" golden variant
    alert: Optional[dict]
    free_text: str
    withheld_source: str  # source removed in the "missing_source" variant
    regulatory: bool = False
    notes: list[str] = field(default_factory=list)


# --- overlay helpers

def degrade_api(b: ScenarioBuilder, service: str, start: float, end: float, *, endpoint: str | None = None,
                error_rate: float | None = None, p95_factor: float | None = None,
                rpm_factor: float | None = None, tag_minute: int | None = None, tag: str | None = None) -> None:
    """Set error rate (at least `error_rate`) and p95 (at least `p95_factor` x baseline) for a period."""
    match = {"endpoint": endpoint} if endpoint else {}
    for row in b.select("API", service, start, end, **match):
        if error_rate is not None:
            row["error_rate"] = round(min(1.0, error_rate * b.rng.uniform(1.0, 1.04)), 4)
        if p95_factor is not None:
            row["p95_latency_ms"] = round(row["_base_p95"] * p95_factor * b.rng.uniform(1.0, 1.05), 1)
        if rpm_factor is not None:
            row["request_count"] = round(row["request_count"] * rpm_factor)
        if tag and row["_minute"] == tag_minute:
            row["_tag"] = tag


def add_change(b: ScenarioBuilder, minute: float, record_id: str, service: str, change_type: str, summary: str,
               *, version: str | None = None, deployment_id: str, author: str,
               rollback_available: bool = True, tag: str | None = None) -> None:
    b.add("DEP", minute, service, tag=tag, record_id=record_id, deployment_id=deployment_id, version=version,
          change_type=change_type, author=author, change_summary=summary, rollback_available=rollback_available)


def add_logs(b: ScenarioBuilder, service: str, start: int, end: int, per_minute: int, level: str,
             message: str | Callable[[int], str], *, error_code: str | None = None, first_tag: str | None = None,
             with_transaction: bool = False) -> None:
    """`per_minute` log lines per minute in [start, end); the first one can be tagged."""
    for minute in range(start, end):
        for _ in range(per_minute):
            text = message(minute) if callable(message) else message
            b.add("LOG", b.event_minute(minute), service, tag=first_tag, level=level, message=text,
                  error_code=error_code, host=b.rng.choice(vocab.HOSTS[service]), trace_id=b.trace_id(),
                  transaction_id=b.next_txn() if with_transaction else None)
            first_tag = None


def add_complaints(b: ScenarioBuilder, product: str, count: int, start: int, end: int, *,
                   texts: list[str] | None = None, first_tag: str | None = None,
                   transaction_ids: list[str] | None = None) -> None:
    """`count` complaints spread evenly over minutes [start, end)."""
    span = end - start
    for i in range(count):
        minute = start + i * span // count
        text = b.rng.choice(texts) if texts else None
        txn = transaction_ids[i] if transaction_ids and i < len(transaction_ids) else None
        add_complaint(b, minute, product, text=text, transaction_id=txn, tag=first_tag)
        first_tag = None


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --- SC-01: bad release on payments-service

def build_sc01(b: ScenarioBuilder) -> None:
    add_change(b, -170, "DEP-0001", "accounts-service", "release",
               "accounts-service v3.8.2: statement export pagination fix",
               version="v3.8.2", deployment_id="CR-24811", author="accounts-release-pipeline")
    add_change(b, -140, "DEP-0003", "api-gateway", "infra",
               "Monthly OS patching of the gateway node pool (rolling, one node at a time)",
               deployment_id="CR-24830", author="platform-gateway-oncall")
    add_change(b, 5, "DEP-0005", "notification-service", "config",
               "SMS template wording update for card transaction alerts",
               deployment_id="CR-24852", author="notifications-config-pipeline", tag="red_herring_change")
    add_change(b, 20, "DEP-0007", "payments-service", "release",
               "payments-service v2.14.0 (from v2.13.4): DB client library upgrade and connection settings "
               "refactor (PAY-1182)",
               version="v2.14.0", deployment_id="CR-24860", author="payments-release-pipeline", tag="causal_change")

    b.add("LOG", 2.2, "payments-service", level="INFO", host="payments-01",
          message="Config audit: db.client.query_timeout_ms=5000, db.client.pool_size=40 (payments-service v2.13.4)")
    b.add("LOG", 20.3, "payments-service", level="INFO", host="payments-01",
          message="Deployment of payments-service v2.14.0 completed on payments-01, payments-02, payments-03")
    b.add("LOG", 20.6, "payments-service", level="INFO", host="payments-02", tag="db_client_config_log",
          message="DB client initialised: pool_size=40, connect_timeout_ms=1000, query_timeout_ms=1000")

    add_logs(b, "payments-service", 30, 90, 10, "ERROR",
             "Query timeout after 1000 ms on core-banking-db (statement=insert_payment); payment rejected",
             error_code="DB_TIMEOUT", first_tag="first_db_timeout_log", with_transaction=True)
    add_logs(b, "api-gateway", 30, 90, 4, "ERROR", "Upstream payments-service returned 500 for POST /payments/v1/payments",
             error_code="UPSTREAM_5XX")

    degrade_api(b, "payments-service", 30, 90, error_rate=0.38, p95_factor=2.6,
                tag_minute=35, tag="payments_error_spike")
    degrade_api(b, "api-gateway", 30, 90, error_rate=0.09, p95_factor=3.5)
    degrade_api(b, "mobile-app", 30, 90, error_rate=0.07, p95_factor=1.6)
    degrade_api(b, "net-banking", 30, 90, error_rate=0.05, p95_factor=1.4)

    add_complaints(b, "fund_transfer", 212, 30, 50, first_tag="first_transfer_complaint")
    add_complaints(b, "fund_transfer", 40, 50, 90)


# --- SC-02: Kafka consumer lag after a broker failure

def build_sc02(b: ScenarioBuilder) -> None:
    add_change(b, -160, "DEP-0009", "kafka-platform", "infra",
               "Broker JVM heap alert threshold raised from 80% to 85%",
               deployment_id="CR-25003", author="eventhub-platform-oncall")
    add_change(b, -100, "DEP-0011", "ledger-consumer", "release",
               "ledger-consumer v1.9.0: batch insert size tuned from 200 to 500 records",
               version="v1.9.0", deployment_id="CR-25010", author="ledger-release-pipeline")
    add_change(b, 11, "DEP-0012", "kafka-platform", "schema",
               "Register marketing.offers-value schema v4 (field promo_code removed)",
               deployment_id="CR-25031", author="marketing-schema-pipeline", tag="red_herring_change")
    b.add("SRG", 12, "kafka-platform", tag="red_herring_schema_warning", subject="marketing.offers-value",
          version=4, compatibility="BACKWARD",
          error="Compatibility warning: field promo_code removed; consumers on v3 ignore the field")

    b.add("LOG", 27.5, "kafka-platform", tag="broker_io_error_log", level="ERROR", host="kafka-broker-2",
          error_code="BROKER_IO_ERROR",
          message="kafka-broker-2: disk I/O error on /data/kafka-logs; broker shutting down")
    b.add("KFK", 28.1, "kafka-platform", tag="broker_down_event", topic="__cluster_metadata", partition=-1,
          event_type="broker_down", error="Broker 2 unreachable; partition leadership moved for 48 partitions")
    b.add("LOG", 53.2, "kafka-platform", level="INFO", host="kafka-broker-2",
          message="kafka-broker-2 rejoined the cluster after disk replacement; replicas catching up")

    for row in b.select("KFK", "kafka-platform", 28, 53, event_type="metric_sample"):
        row.update(under_replicated=6, isr_count=2)
        if row["_minute"] == 33:
            row["_tag"] = "urp_sample"
    for row in b.select("QRM", None, 28, 53):
        row["session_expirations"] = 1

    peaks = {"ledger-consumer": 85_000, "notification-service": 52_000}
    for service, peak in peaks.items():
        for row in b.select("KFK", service, 29, 90, event_type="metric_sample"):
            m = row["_minute"]
            lag = 400 + (peak - 400) * (m - 29) / 26 if m <= 55 else peak * (1 - 0.02 * (m - 55))
            row["lag"] = round(lag * b.rng.uniform(0.98, 1.0)) if m != 55 else peak
            if service == "ledger-consumer" and m == 55:
                row["_tag"] = "peak_lag_sample"

    first = "first_rebalance"
    for minute in range(29, 57, 3):
        for service, group in vocab.CONSUMER_GROUPS.items():
            b.add("KFK", b.event_minute(minute), service, tag=first, topic=vocab.PAYMENTS_TOPIC, partition=-1,
                  consumer_group=group, event_type="rebalance",
                  error="Group rebalance triggered: member left the group (session timeout)")
            first = None

    add_logs(b, "ledger-consumer", 29, 57, 2, "WARN",
             lambda m: f"Consumer group ledger-consumer-cg rebalancing (generation {214 + (m - 29) // 3})",
             error_code="CONSUMER_REBALANCE")
    add_logs(b, "ledger-consumer", 30, 57, 1, "ERROR",
             "CommitFailedException: commit cannot be completed since the group has already rebalanced",
             error_code="COMMIT_FAILED")
    add_logs(b, "notification-service", 32, 90, 2, "WARN",
             lambda m: f"SMS dispatch delayed: oldest event age {60 * (m - 30)} s",
             error_code="DISPATCH_DELAYED")

    degrade_api(b, "notification-service", 30, 90, error_rate=0.01, p95_factor=1.8)
    add_complaints(b, "balance_and_alerts", 70, 35, 65, first_tag="first_balance_complaint")


# --- SC-03: core banking DB saturation

def build_sc03(b: ScenarioBuilder) -> None:
    add_change(b, -150, "DEP-0019", "core-banking-db", "infra",
               "Storage volume for cbdb-prod-01 expanded from 4 TB to 6 TB",
               deployment_id="CR-25204", author="core-banking-dba-oncall", rollback_available=False)
    add_change(b, -45, "DEP-0021", "core-banking-db", "config",
               "EOD reconciliation batch schedule moved from 22:00 to 18:00 UTC to meet the new reporting "
               "cut-off (OPS-771)",
               deployment_id="CR-25230", author="batch-scheduler-config", tag="causal_change")
    add_change(b, 5, "DEP-0023", "mobile-app", "release", "mobile-app v5.2.1: UI copy and icon updates",
               version="v5.2.1", deployment_id="CR-25241", author="mobile-release-pipeline", tag="red_herring_change")

    b.add("LOG", 28.0, "core-banking-db", tag="batch_start_log", level="INFO", host="cbdb-prod-01",
          error_code="BATCH_JOB_STARTED", message="Batch job EOD_RECON started (schedule 18:00 UTC), parallel workers=24")
    b.add("LOG", 28.4, "core-banking-db", level="INFO", host="cbdb-prod-01", error_code="BATCH_JOB_STARTED",
          message="Batch job INTEREST_ACCRUAL started (schedule 18:00 UTC), parallel workers=16")

    for row in b.select("DBM", None, 29, 90):
        m = row["_minute"]
        used = 0.6 if m == 29 else 0.84 if m == 30 else 0.98
        row.update(active_connections=round(500 * used) - (b.rng.randint(0, 3) if m > 30 else 0),
                   cpu_pct=round(b.rng.uniform(90.5, 93.5), 1) if m > 30 else 78.0,
                   mem_pct=round(b.rng.uniform(77, 80), 1),
                   slow_query_count=b.rng.randint(40, 60),
                   lock_wait_ms=round(35.0 * b.rng.uniform(10, 12), 1))
        if m == 32:
            row["_tag"] = "db_saturation_sample"

    degrade_api(b, "auth-service", 30, 90, error_rate=0.06, p95_factor=5.0, tag_minute=34, tag="auth_latency_spike")
    degrade_api(b, "accounts-service", 30, 90, error_rate=0.06, p95_factor=4.5)
    degrade_api(b, "api-gateway", 30, 90, error_rate=0.03, p95_factor=2.5)
    degrade_api(b, "mobile-app", 30, 90, error_rate=0.03, p95_factor=2.0)
    degrade_api(b, "net-banking", 30, 90, error_rate=0.03, p95_factor=2.0)
    degrade_api(b, "payments-service", 30, 90, error_rate=0.02, p95_factor=1.8)

    add_logs(b, "accounts-service", 30, 90, 4, "ERROR",
             "Connection pool exhausted: waited 2000 ms for a connection to core-banking-db",
             error_code="CONN_POOL_EXHAUSTED", first_tag="first_pool_exhausted_log")
    add_logs(b, "auth-service", 30, 90, 3, "ERROR", "Timeout reading customer profile from core-banking-db",
             error_code="DB_TIMEOUT")
    add_logs(b, "core-banking-db", 30, 90, 3, "WARN", "Lock wait timeout exceeded on table accounts_balance",
             error_code="LOCK_WAIT_TIMEOUT")

    add_complaints(b, "login", 30, 35, 65, first_tag="first_login_complaint")


# --- SC-04: expired TLS certificate on the card route to the payment switch

def build_sc04(b: ScenarioBuilder) -> None:
    add_change(b, -160, "DEP-0029", "payment-network-gateway", "infra",
               "Firewall rule review: no functional change", deployment_id="CR-25402",
               author="network-security-oncall", rollback_available=False)
    add_change(b, -120, "DEP-0031", "payment-network-gateway", "release",
               "payment-network-gateway v4.3.0: UPI retry tuning", version="v4.3.0",
               deployment_id="CR-25409", author="payments-network-release-pipeline")
    add_change(b, 10, "DEP-0033", "accounts-service", "config",
               "Feature flag balance_cache_ttl_v2 enabled for 10% of traffic",
               deployment_id="CR-25433", author="accounts-config-pipeline", tag="red_herring_change")

    expiry = b.at(30)
    for row in b.select("NET", "payment-network-gateway", float("-inf"), float("inf"), route="card"):
        row["cert_expiry"] = expiry
        if row["_minute"] >= 30:
            row.update(tls_status="expired", latency_ms=round(b.rng.uniform(8, 15), 1))
            if row["_minute"] == 30:
                row["_tag"] = "tls_expired_event"

    for minute in (0, 15):
        b.add("LOG", minute + 0.5, "payment-network-gateway", level="WARN", host="pngw-01",
              error_code="CERT_EXPIRY_SOON",
              message=f"Client certificate for external-switch card route expires in {30 - minute} minutes "
                      f"(notAfter={expiry:%Y-%m-%dT%H:%M:%SZ})")
    add_logs(b, "payment-network-gateway", 30, 90, 6, "ERROR",
             f"TLS handshake to external-switch (card route) failed: certificate has expired "
             f"(notAfter={expiry:%Y-%m-%dT%H:%M:%SZ})",
             error_code="CERT_EXPIRED", first_tag="cert_expired_log")
    add_logs(b, "payments-service", 30, 90, 3, "ERROR", "Card authorisation failed: payment-network-gateway "
             "returned ROUTE_UNAVAILABLE for route card", error_code="ROUTE_UNAVAILABLE", with_transaction=True)

    degrade_api(b, "payment-network-gateway", 30, 90, endpoint="/routes/card", error_rate=1.0,
                tag_minute=33, tag="card_route_failure")
    degrade_api(b, "payments-service", 30, 90, error_rate=0.30, tag_minute=33, tag="payments_error_spike")
    degrade_api(b, "api-gateway", 30, 90, error_rate=0.07)
    degrade_api(b, "mobile-app", 30, 90, error_rate=0.05)

    declined = [t for t in vocab.COMPLAINT_TEXTS["card_payment"] if "declined" in t or "failed" in t]
    add_complaints(b, "card_payment", 40, 32, 62, texts=declined, first_tag="first_card_complaint")


# --- SC-05: silent duplicate debits after a retry-policy change

def build_sc05(b: ScenarioBuilder) -> None:
    add_change(b, -160, "DEP-0039", "core-banking-db", "infra", "Read replica added for reporting queries",
               deployment_id="CR-25601", author="core-banking-dba-oncall", rollback_available=False)
    add_change(b, -90, "DEP-0041", "payments-service", "config",
               "Retry policy for payment-network-gateway calls: max_attempts 1 -> 3, retry on gateway "
               "timeout (PAY-1207)",
               deployment_id="CR-25622", author="payments-config-pipeline", tag="causal_change")
    add_change(b, 15, "DEP-0043", "net-banking", "release", "net-banking v8.1.0: accessibility fixes",
               version="v8.1.0", deployment_id="CR-25640", author="web-release-pipeline", tag="red_herring_change")

    # Gateway timeouts on some minutes trigger the new retries.
    for row in b.select("NET", "payment-network-gateway", 18, 90, route="card"):
        if b.rng.random() < 0.2:
            row["latency_ms"] = round(b.rng.uniform(1100, 1400), 1)

    add_logs(b, "payments-service", 18, 90, 3, "WARN",
             "Retrying payment submission to payment-network-gateway (attempt 2/3) after 1000 ms timeout",
             error_code="PAYMENT_RETRY", with_transaction=True)
    add_logs(b, "payments-service", 20, 90, 2, "WARN",
             "Idempotency key missing on retried submission; request forwarded as a new payment",
             error_code="IDEMPOTENCY_KEY_MISSING", first_tag="first_idempotency_log", with_transaction=True)

    produced = b.select("KFK", "payments-service", 20, 86, event_type="produced")
    duplicated = b.rng.sample(produced, 140)
    duplicated.sort(key=lambda r: r["_minute"])
    first = "first_duplicate_event"
    for original in duplicated:
        b.add("KFK", original["_minute"] + b.rng.randint(2, 30) / 60, "payments-service", tag=first,
              topic=vocab.PAYMENTS_TOPIC, partition=original["partition"], event_type="produced",
              transaction_id=original["transaction_id"])
        first = None

    degrade_api(b, "payments-service", 18, 90, rpm_factor=1.06)

    # 65 complaints in minutes 20-49: 30 name a duplicated transaction (filed a few minutes after it), 35 do not.
    double = [t for t in vocab.COMPLAINT_TEXTS["card_payment"] if "twice" in t or "two times" in t or "Double" in t]
    first = "linked_double_charge_complaint"
    for original in duplicated[:30]:
        add_complaint(b, min(49, int(original["_minute"]) + 3), "card_payment", text=b.rng.choice(double),
                      transaction_id=original["transaction_id"], tag=first)
        first = None
    add_complaints(b, "card_payment", 35, 20, 50, texts=double)
    add_complaints(b, "card_payment", 25, 50, 90, texts=double)


SCENARIOS: dict[str, ScenarioDef] = {
    "SC-01": ScenarioDef(
        scenario_id="SC-01", title="Bad release on payments-service",
        start=utc(2026, 3, 14, 9, 35), seed=2026031401, onset_minute=30, reference_minute=33, build=build_sc01,
        issue_class="release_regression", expected_severity="S1",
        root_cause="Release v2.14.0 of payments-service (DEP-0007) reduced the DB client query timeout from "
                   "5000 ms to 1000 ms, so payment inserts time out under normal load",
        runbook_id="RB-PAY-003", expected_doc_ids=["RB-PAY-003", "PM-2025-011"],
        causal_change_tag="causal_change",
        must_cite_tags=["causal_change", "first_db_timeout_log", "payments_error_spike"],
        red_herring_tags=["red_herring_change"],
        expected_rules_pass1=["R-S1-ERR", "R-S2-CMP"],
        replay_mode="alert_json",
        alert={"alert_name": "PaymentsErrorRateHigh", "service": "payments-service",
               "condition": "error rate > 15% for 3 min", "reported_severity": "S1"},
        free_text="Customers say fund transfers in the app keep failing with a generic error since about "
                  "10:05 UTC.",
        withheld_source="DEP",
    ),
    "SC-02": ScenarioDef(
        scenario_id="SC-02", title="Kafka consumer lag after broker failure",
        start=utc(2026, 3, 21, 12, 30), seed=2026032102, onset_minute=30, reference_minute=45, build=build_sc02,
        issue_class="eventhub_broker", expected_severity="S2",
        root_cause="Failure of kafka-broker-2 (disk I/O error) left 6 partitions under-replicated and set off "
                   "repeated consumer-group rebalances, so ledger-consumer and notification-service fell "
                   "behind on payments.transactions",
        runbook_id="RB-KFK-001", expected_doc_ids=["RB-KFK-001", "PM-2025-014"],
        causal_change_tag=None,
        must_cite_tags=["broker_down_event", "urp_sample", "peak_lag_sample"],
        red_herring_tags=["red_herring_change", "red_herring_schema_warning"],
        expected_rules_pass1=["R-S2-URP", "R-S2-LAG", "R-S2-CMP"],
        replay_mode="detection_replay", alert=None,
        free_text="Customers not seeing balance updates and no SMS alerts since about 13:00 UTC.",
        withheld_source="KFK",
    ),
    "SC-03": ScenarioDef(
        scenario_id="SC-03", title="Core banking DB saturation",
        start=utc(2026, 3, 27, 17, 32), seed=2026032703, onset_minute=30, reference_minute=35, build=build_sc03,
        issue_class="database_capacity", expected_severity="S1",
        root_cause="The EOD reconciliation and interest accrual batch jobs, moved to 18:00 UTC by DEP-0021, "
                   "ran during peak traffic, exhausted the core-banking-db connection pool and locked hot "
                   "tables",
        runbook_id="RB-DB-002", expected_doc_ids=["RB-DB-002", "PM-2025-019"],
        causal_change_tag="causal_change",
        must_cite_tags=["batch_start_log", "db_saturation_sample", "first_pool_exhausted_log"],
        red_herring_tags=["red_herring_change"],
        expected_rules_pass1=["R-S1-LAT"],
        replay_mode="alert_json",
        alert={"alert_name": "LoginAndBalanceLatencyHigh", "service": "auth-service",
               "condition": "p95 latency > 3x baseline on auth-service and accounts-service",
               "reported_severity": "S2"},
        free_text="Login and balance checks are very slow for customers in the app and net banking since "
                  "about 18:02 UTC.",
        withheld_source="DBM",
    ),
    "SC-04": ScenarioDef(
        scenario_id="SC-04", title="Expired TLS certificate on the card payment route",
        start=utc(2026, 4, 2, 7, 30), seed=2026040204, onset_minute=30, reference_minute=33, build=build_sc04,
        issue_class="network_tls", expected_severity="S1",
        root_cause="The client certificate for the card route from payment-network-gateway to the external "
                   "payment switch expired at 08:00 UTC, so every TLS handshake on that route fails",
        runbook_id="RB-NET-004", expected_doc_ids=["RB-NET-004", "PM-2025-023"],
        causal_change_tag=None,
        must_cite_tags=["tls_expired_event", "cert_expired_log", "card_route_failure"],
        red_herring_tags=["red_herring_change"],
        expected_rules_pass1=["R-S1-ROUTE", "R-S1-ERR"],
        replay_mode="alert_json",
        alert={"alert_name": "CardRouteFailures", "service": "payment-network-gateway",
               "condition": "card route failure rate >= 50% for 3 min", "reported_severity": "S1"},
        free_text="Card payments are failing at the external switch since 08:00 UTC; UPI and NEFT look fine.",
        withheld_source="NET",
    ),
    "SC-05": ScenarioDef(
        scenario_id="SC-05", title="Silent duplicate debits after a retry-policy change",
        start=utc(2026, 4, 9, 10, 0), seed=2026040905, onset_minute=30, reference_minute=50, build=build_sc05,
        issue_class="data_integrity", expected_severity="S2",
        root_cause="The retry-policy change DEP-0041 (max_attempts 1 to 3 on gateway timeout) resubmitted "
                   "payments without an idempotency key, creating duplicate debits",
        runbook_id="RB-PAY-005", expected_doc_ids=["RB-PAY-005", "REG-001", "PM-2025-027"],
        causal_change_tag="causal_change",
        must_cite_tags=["causal_change", "first_duplicate_event", "first_idempotency_log"],
        red_herring_tags=["red_herring_change"],
        expected_rules_pass1=["R-S2-DUP", "R-S2-CMP"],
        replay_mode="detection_replay", alert=None,
        free_text="Customers complaining about being charged twice for card payments since about 10:20 UTC.",
        withheld_source="KFK", regulatory=True,
        notes=["R-P2-INTEG fires in pass 2 when triage returns data_integrity (Architecture Spec Section 16.2)"],
    ),
}

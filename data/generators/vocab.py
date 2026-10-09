"""Fixed vocabularies for the synthetic data: service profiles, noise messages,
complaint texts and synthetic personal data (Architecture Spec Section 16.1)."""

from __future__ import annotations

# --- API metrics: (service, endpoint) -> (requests per minute, p95 ms, error rate)
API_PROFILES: dict[tuple[str, str], tuple[int, float, float]] = {
    ("mobile-app", "/app/v1/session"): (1200, 420.0, 0.004),
    ("net-banking", "/web/v1/session"): (800, 380.0, 0.004),
    ("api-gateway", "/gw/v1/route"): (2600, 180.0, 0.003),
    ("auth-service", "/auth/v1/login"): (900, 120.0, 0.002),
    ("payments-service", "/payments/v1/payments"): (600, 260.0, 0.005),
    ("accounts-service", "/accounts/v1/balance"): (1100, 150.0, 0.003),
    ("notification-service", "/notify/v1/send"): (500, 90.0, 0.002),
    # One endpoint per payment route (Architecture Spec Section 5.3.2).
    ("payment-network-gateway", "/routes/card"): (180, 340.0, 0.004),
    ("payment-network-gateway", "/routes/upi"): (220, 300.0, 0.004),
    ("payment-network-gateway", "/routes/neft"): (60, 450.0, 0.004),
}

# --- DB / infrastructure metrics for core-banking-db
DB_INSTANCE = "cbdb-prod-01"
DB_PROFILE = {
    "active_connections": 180, "max_connections": 500, "cpu_pct": 45.0, "mem_pct": 62.0,
    "slow_query_count": 2, "lock_wait_ms": 35.0,
}

# --- Kafka (EventHub)
PAYMENTS_TOPIC = "payments.transactions"
TOPIC_PARTITIONS = 12
CONSUMER_GROUPS = {"ledger-consumer": "ledger-consumer-cg", "notification-service": "notification-cg"}
CONNECTORS = [("ledger-sink-jdbc", 2), ("notification-sms-sink", 1)]
SCHEMA_SUBJECTS = [("payments.transactions-value", 7), ("accounts.balance-updates-value", 3)]
ACL_ENTRIES = [
    ("User:svc-ledger-consumer", "Topic:payments.transactions", "READ"),
    ("User:svc-payments", "Topic:payments.transactions", "WRITE"),
    ("User:svc-notification", "Topic:payments.transactions", "READ"),
]

# --- Network: (source, destination, route) -> latency ms
NETWORK_ROUTES: dict[tuple[str, str, str], float] = {
    ("payment-network-gateway", "external-switch", "card"): 85.0,
    ("payment-network-gateway", "external-switch", "upi"): 70.0,
    ("payment-network-gateway", "external-switch", "neft"): 120.0,
    ("api-gateway", "auth-service", "internal-mtls"): 4.0,
}

HOSTS = {
    "mobile-app": ["mobile-bff-01", "mobile-bff-02"],
    "net-banking": ["web-bff-01", "web-bff-02"],
    "api-gateway": ["gw-01", "gw-02", "gw-03"],
    "auth-service": ["auth-01", "auth-02"],
    "payments-service": ["payments-01", "payments-02", "payments-03"],
    "accounts-service": ["accounts-01", "accounts-02"],
    "notification-service": ["notify-01", "notify-02"],
    "ledger-consumer": ["ledger-01", "ledger-02"],
    "core-banking-db": ["cbdb-prod-01"],
    "payment-network-gateway": ["pngw-01", "pngw-02"],
    "kafka-platform": ["kafka-broker-1", "kafka-broker-2", "kafka-broker-3"],
}

# --- Background log noise: (service, level, message, error_code)
NOISE_LOGS: list[tuple[str, str, str, str | None]] = [
    ("api-gateway", "WARN", "Slow upstream response from accounts-service (612 ms)", None),
    ("api-gateway", "WARN", "Rate limit close to quota for client app-ios (82%)", None),
    ("mobile-app", "WARN", "Push token refresh failed for device; will retry", None),
    ("net-banking", "WARN", "Session cookie renewed after clock skew of 4 s", None),
    ("auth-service", "WARN", "OTP delivery retried once for login attempt", None),
    ("accounts-service", "WARN", "Cache miss ratio above 20% for balance cache", "CACHE_MISS_HIGH"),
    ("notification-service", "WARN", "Email provider responded slowly (1.2 s)", None),
    ("ledger-consumer", "WARN", "GC pause of 210 ms", None),
    ("payments-service", "WARN", "FX rate cache older than 5 minutes; refreshing", None),
    ("payment-network-gateway", "WARN", "Switch heartbeat delayed by 300 ms", None),
    ("kafka-platform", "WARN", "Log segment roll took 450 ms on kafka-broker-3", None),
    ("core-banking-db", "WARN", "Autovacuum running on table txn_audit", None),
]
NOISE_INFO: list[tuple[str, str]] = [
    ("payments-service", "Payment batch settled with switch"),
    ("accounts-service", "Balance cache warmed"),
    ("api-gateway", "Configuration reloaded (no changes)"),
    ("ledger-consumer", "Committed offsets for payments.transactions"),
    ("notification-service", "Template cache refreshed"),
]

# --- Complaints
PRODUCTS = ["fund_transfer", "balance_and_alerts", "card_payment", "login", "bill_payment", "account_statement"]
CHANNELS = ["app", "call_centre", "social"]

COMPLAINT_TEXTS: dict[str, list[str]] = {
    "fund_transfer": [
        "My transfer to my landlord failed twice in the app and I got a generic error.",
        "Fund transfer keeps failing with 'something went wrong'. Is the bank down?",
        "Tried to send money three times, all failed. Very frustrating.",
        "Transfer stuck on processing and then failed. Money not sent.",
        "Cannot move money between my accounts, the app just times out.",
        "Payment to a friend failed, please fix this urgently.",
    ],
    "balance_and_alerts": [
        "My balance has not updated since this afternoon even though I paid for groceries.",
        "I did not get the SMS alert for my card payment.",
        "Balance in the app looks wrong, my last two transactions are missing.",
        "No transaction alerts today, I usually get them instantly.",
        "Balance shows old amount, I am worried my salary is missing.",
    ],
    "card_payment": [
        "My card was declined at the shop but I have enough money.",
        "Online card payment failed at checkout, tried twice.",
        "I was charged twice for the same card payment at the supermarket.",
        "Same payment appears two times on my statement, please refund one.",
        "Card payment declined abroad and at home today.",
        "Double charge for my train ticket, this is not acceptable.",
    ],
    "login": [
        "The app is very slow to log in, it took two minutes.",
        "Login page keeps spinning and then times out.",
        "Cannot see my balance, the screen just loads forever.",
        "Net banking login extremely slow this evening.",
    ],
    "bill_payment": [
        "Bill payment confirmation email never arrived.",
        "Utility bill payment shows pending for hours.",
    ],
    "account_statement": [
        "Statement download fails with an error.",
        "Monthly statement is missing a transaction.",
    ],
}

# Synthetic names for complaint text; redaction must remove them (Architecture Spec Section 9.2).
from src.safety.pii_names import SYNTHETIC_NAMES  # noqa: E402,F401


def synthetic_phone(rng) -> str:
    """UK phone number in the range Ofcom reserves for drama, so it is never real."""
    return f"07700 900{rng.randint(0, 999):03d}"


def synthetic_account(rng) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(8))

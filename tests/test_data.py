"""Synthetic data (Architecture Spec Section 16; Req. Sections 9.2 to 9.4, 10.6, 12.1)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

import pydantic
import pytest
import yaml

from data.generators import FIXTURES, SCENARIOS
from data.generators.builder import SOURCE_FILES
from data.generators.fixtures import INJECTION_TEXTS
from src.config import DEFAULT_CUSTOMER_FACING_SERVICES, REPO_ROOT, SCENARIO_DIRS
from src.schemas.enums import InputMode, IssueClass, SeverityLevel, TelemetrySource
from src.schemas.evidence import RECORD_MODELS
from tests.data_helpers import Signals, highest, load, manifest, minute_of, rule_severities, scenario_path

DATA = REPO_ROOT / "data"
ALL_DATASETS = [*SCENARIOS, *FIXTURES]


def read_json(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


# --- generator output is committed and up to date

def test_committed_data_matches_generator(capsys):
    """Regenerates everything into a temporary directory and compares byte for byte."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("generate_data", REPO_ROOT / "scripts" / "generate_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check() == 0, capsys.readouterr().out


# --- every record matches the schemas

@pytest.mark.parametrize("dataset_id", ALL_DATASETS)
def test_records_validate_against_schemas(dataset_id):
    for src in SOURCE_FILES:
        records = load(dataset_id, src)
        model = RECORD_MODELS[TelemetrySource(src)]
        ids = [r["record_id"] for r in records]
        assert len(ids) == len(set(ids)), f"{src}: duplicate record IDs"
        assert all(i.startswith(f"{src}-") for i in ids), src
        if dataset_id == "FX-FAILURE" and src == "LOG":
            continue  # broken on purpose; see test_failure_fixture
        for record in records:
            model.model_validate(record)


@pytest.mark.parametrize("dataset_id", ALL_DATASETS)
def test_manifest_evidence_exists(dataset_id):
    m = manifest(dataset_id)
    ids = {r["record_id"] for src in SOURCE_FILES for r in load(dataset_id, src)}
    assert set(m["key_evidence"].values()) <= ids
    assert m["window_start"] < m["onset"] < m["window_end"]
    assert m["record_counts"] == {rel: len(load(dataset_id, src)) for src, rel in SOURCE_FILES.items()}


# --- scenarios produce the signal values of Architecture Spec Section 16.2

@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_expected_rules_fire(scenario_id):
    """The draft rules (Req. 10.6) reach each scenario's expected severity (Req. 9.4)."""
    s = SCENARIOS[scenario_id]
    fired = Signals(scenario_id).fired_pass1()
    expected = set(s.expected_rules_pass1)
    # R-S3-DEG may fire alongside more severe rules; it never decides the result.
    assert fired - {"R-S3-DEG"} == expected
    severities = rule_severities()
    assert highest({severities[r] for r in fired}) == s.expected_severity


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_missing_source_variant_keeps_expected_severity(scenario_id):
    """With the deciding source withheld, another rule of the same severity still fires (Section 16.4)."""
    s = SCENARIOS[scenario_id]
    sig = Signals(scenario_id)
    attribute = {"API": "api", "KFK": "kafka", "QRM": "quorum", "CMP": "complaints"}.get(s.withheld_source)
    if attribute:
        setattr(sig, attribute, [])
    severities = rule_severities()
    assert highest({severities[r] for r in sig.fired_pass1()}) == s.expected_severity


def test_sc01_values():
    sig = Signals("SC-01")
    payments = sig.api_series("payments-service", "error_rate")
    assert all(payments[m] >= 0.38 for m in range(30, 50)) and max(payments[m] for m in range(20)) < 0.01
    assert min(sig.p95_ratio("api-gateway")[m] for m in range(30, 90)) >= 3.4
    fund = [c for c in sig.complaints if c["product"] == "fund_transfer" and 30 <= minute_of(c, sig.start) < 50]
    assert len(fund) >= 212
    changes = {r["record_id"]: r for r in load("SC-01", "DEP")}
    assert minute_of(changes["DEP-0007"], sig.start) == 20 and changes["DEP-0007"]["change_type"] == "release"
    assert changes["DEP-0005"]["service"] == "notification-service" and changes["DEP-0005"]["change_type"] == "config"
    db = load("SC-01", "DBM")
    assert max(r["active_connections"] / r["max_connections"] for r in db) < 0.5  # DB normal (Req. 9.4)


def test_sc02_values():
    sig = Signals("SC-02")
    down = [r for r in sig.kafka if r["event_type"] == "broker_down"]
    assert len(down) == 1 and minute_of(down[0], sig.start) == 28
    urp = sig.kafka_series("under_replicated", service="kafka-platform", event_type="metric_sample")
    assert sum(1 for v in urp.values() if v == 6) == 25
    assert max(sig.kafka_series("lag", topic="payments.transactions", event_type="metric_sample").values()) == 85_000
    assert max(r["offline_partitions"] for r in sig.quorum) == 0
    assert all(max(sig.api_series(s, "error_rate").values()) < 0.02 for s in DEFAULT_CUSTOMER_FACING_SERVICES)
    assert sig.complaints_per_product_30min()["balance_and_alerts"] >= 70
    assert sum(1 for r in sig.kafka if r["event_type"] == "rebalance") >= 10
    assert any(r.get("error") and r["subject"] == "marketing.offers-value" for r in load("SC-02", "SRG"))


def test_sc03_values():
    sig = Signals("SC-03")
    db = [r for r in load("SC-03", "DBM") if minute_of(r, sig.start) >= 31]
    assert min(r["active_connections"] / r["max_connections"] for r in db) >= 0.97
    assert min(r["cpu_pct"] for r in db) >= 90
    assert min(r["lock_wait_ms"] for r in db) >= 350  # about 10x the 35 ms baseline
    for service in ("auth-service", "accounts-service"):
        ratios = [sig.p95_ratio(service)[m] for m in range(30, 50)]
        assert min(ratios) >= 4 and max(ratios) <= 6.5
        assert max(sig.api_series(service, "error_rate").values()) < 0.15
    logs = load("SC-03", "LOG")
    batch = [r for r in logs if r.get("error_code") == "BATCH_JOB_STARTED"]
    assert batch and minute_of(batch[0], sig.start) == 28
    herring = {r["record_id"]: r for r in load("SC-03", "DEP")}["DEP-0023"]
    assert herring["service"] == "mobile-app" and herring["version"] == "v5.2.1"


def test_sc04_values():
    sig = Signals("SC-04")
    card = sig.api_series("payment-network-gateway", "error_rate", "/routes/card")
    assert all(card[m] == 1.0 for m in range(30, 90))
    for route in ("/routes/upi", "/routes/neft"):
        assert max(sig.api_series("payment-network-gateway", "error_rate", route).values()) < 0.01
    net = [r for r in load("SC-04", "NET") if r["route"] == "card" and minute_of(r, sig.start) >= 30]
    assert net and all(r["tls_status"] == "expired" for r in net)
    assert all(r["tls_status"] == "ok" for r in load("SC-04", "NET") if r["route"] != "card")
    assert any(r.get("error_code") == "CERT_EXPIRED" for r in load("SC-04", "LOG"))
    assert min(sig.api_series("payments-service", "error_rate")[m] for m in range(30, 90)) >= 0.30
    assert all(r["active_connections"] < 250 for r in load("SC-04", "DBM"))


def test_sc05_values():
    sig = Signals("SC-05")
    assert sig.duplicate_transaction_ids() == 140
    retry = {r["record_id"]: r for r in load("SC-05", "DEP")}["DEP-0041"]
    assert minute_of(retry, sig.start) == -90 and retry["change_type"] == "config"
    assert any(r.get("error_code") == "IDEMPOTENCY_KEY_MISSING" for r in load("SC-05", "LOG"))
    cards = [c for c in sig.complaints if c["product"] == "card_payment" and 20 <= minute_of(c, sig.start) < 50]
    assert len(cards) >= 65
    assert all(max(sig.api_series(s, "error_rate").values()) < 0.02 for s in DEFAULT_CUSTOMER_FACING_SERVICES)
    assert all(max(sig.p95_ratio(s).values()) < 1.5 for s in DEFAULT_CUSTOMER_FACING_SERVICES)
    # Complaints lead: the first double-charge complaint comes before the onset at minute 30.
    assert min(minute_of(c, sig.start) for c in cards) < 30


@pytest.mark.parametrize("scenario_id", ["SC-01", "SC-03", "SC-04", "SC-05"])
def test_eventhub_files_healthy_when_platform_not_involved(scenario_id):
    """Req. 9.2: the EventHub files exist with healthy records, so the RCA Agent must rule the platform out."""
    assert all(r["state"] == "RUNNING" for r in load(scenario_id, "CON"))
    assert all(r["result"] == "ALLOWED" for r in load(scenario_id, "ACL"))
    assert all("error" not in r for r in load(scenario_id, "SRG"))
    assert all(r["quorum_healthy"] and r["offline_partitions"] == 0 and r["session_expirations"] == 0
               for r in load(scenario_id, "QRM"))
    assert all(r.get("under_replicated", 0) == 0 for r in load(scenario_id, "KFK"))


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_one_red_herring_change_and_healthy_baseline(scenario_id):
    m = manifest(scenario_id)
    assert m["red_herring_ids"] and all(i.startswith(("DEP-", "SRG-")) for i in m["red_herring_ids"])
    sig = Signals(scenario_id)
    for service in DEFAULT_CUSTOMER_FACING_SERVICES:
        assert max(v for k, v in sig.api_series(service, "error_rate").items() if k < 20) < 0.01


def test_complaints_carry_pseudonymous_refs_and_some_synthetic_pii():
    complaints = load("SC-01", "CMP")
    assert all(c["customer_ref"].startswith("CUST-") for c in complaints)
    assert any("07700 900" in c["text"] for c in complaints)  # Ofcom drama range: never a real number


# --- fixtures (Architecture Spec Section 16.3)

def test_failure_fixture():
    logs = load("FX-FAILURE", "LOG")
    assert logs and all("service" not in r for r in logs)
    with pytest.raises(pydantic.ValidationError):
        RECORD_MODELS[TelemetrySource.LOG].model_validate(logs[0])
    for src in SOURCE_FILES:
        if src != "LOG":
            assert load("FX-FAILURE", src) == load("SC-01", src)


def test_llm_down_fixture():
    flags = json.loads((scenario_path("FX-LLM-DOWN") / "fixture.json").read_text(encoding="utf-8"))
    assert flags == {"llm_enabled": False}
    assert all(load("FX-LLM-DOWN", src) == load("SC-01", src) for src in SOURCE_FILES)


def test_alert_storm_fixture():
    base, storm = load("SC-02", "KFK"), load("FX-ALERT-STORM", "KFK")
    assert len(storm) - len(base) == 200 == manifest("FX-ALERT-STORM")["storm_records_added"]
    start = manifest("FX-ALERT-STORM")["window_start"]
    extra = [r for r in storm if r["partition"] >= 0 and r["event_type"] == "metric_sample"]
    assert len(extra) == 200
    assert {r["service"] for r in extra} == {"kafka-platform", "ledger-consumer", "notification-service"}
    assert all(28 <= minute_of(r, start) < 38 for r in extra)
    assert all(r.get("under_replicated", 0) > 0 or r.get("lag", 0) > 10_000 for r in extra)


def test_injection_fixture():
    m = manifest("FX-INJECTION")
    texts = {
        "LOG": {r["record_id"]: r["message"] for r in load("FX-INJECTION", "LOG")},
        "CMP": {r["record_id"]: r["text"] for r in load("FX-INJECTION", "CMP")},
        "CON": {r["record_id"]: r.get("trace_excerpt") for r in load("FX-INJECTION", "CON")},
    }
    for tag, kind in (("injection_log", "log"), ("injection_complaint", "complaint"), ("injection_connect", "connect")):
        evidence_id = m["key_evidence"][tag]
        assert INJECTION_TEXTS[kind] in texts[evidence_id.split("-")[0]][evidence_id]
    # The base scenario has no injected text.
    assert not any("INSTRUCTION" in r["message"] or "AI ASSISTANT" in r["message"] for r in load("SC-03", "LOG"))


# --- reference data (Architecture Spec Section 16.4)

def test_service_dependencies_match_requirements_section_9_3():
    deps = read_json("service_dependencies.json")
    expected = {
        "mobile-app": {"api-gateway"}, "net-banking": {"api-gateway"},
        "api-gateway": {"auth-service", "payments-service", "accounts-service"},
        "payments-service": {"core-banking-db", "payment-network-gateway", "kafka-platform"},
        "accounts-service": {"core-banking-db"},
        "ledger-consumer": {"kafka-platform", "core-banking-db"},
        "notification-service": {"kafka-platform"},
    }
    for service, depends_on in expected.items():
        assert set(deps[service]["depends_on"]) == depends_on, service
    assert all(target in deps for info in deps.values() for target in info["depends_on"])
    assert {s for s, info in deps.items() if info["customer_facing"]} == set(DEFAULT_CUSTOMER_FACING_SERVICES)
    assert all(info["owner_team"] for info in deps.values())


def test_every_telemetry_service_is_in_the_dependency_map():
    deps = read_json("service_dependencies.json")
    for scenario_id in SCENARIOS:
        for src in SOURCE_FILES:
            assert {r["service"] for r in load(scenario_id, src)} <= set(deps), (scenario_id, src)


def test_stakeholders_cover_every_service_and_group():
    deps = read_json("service_dependencies.json")
    rows = read_json("stakeholders.json")
    covered = {(r["service"], r["severity_group"]) for r in rows}
    assert covered == {(s, g) for s in deps for g in ("major", "low_medium")}
    assert all(r["email"].endswith("@bank.example") and r["chat_handle"].startswith("@") for r in rows)


def test_severity_rules_file():
    data = yaml.safe_load((DATA / "severity_rules.yaml").read_text(encoding="utf-8"))
    assert data["version"] == "rules-v1"
    rules = {r["id"]: r for r in data["rules"]}
    assert set(rules) == {"R-S1-ERR", "R-S1-ROUTE", "R-S1-LAT", "R-S1-OFFLINE", "R-S2-URP", "R-S2-LAG",
                          "R-S2-DUP", "R-S2-CMP", "R-S3-DEG", "R-P2-INTEG", "R-P2-CHANNEL"}
    signals = {"customer_error_rate_pct", "payment_route_failure_pct", "latency_ratio_services_over_3x",
               "offline_partitions", "under_replicated_partitions", "consumer_lag_payments",
               "duplicate_transaction_ids", "complaints_per_product_30min", "single_service_degraded"}
    for rule in rules.values():
        if rule["pass"] == 1:
            assert rule["signal"] in signals and SeverityLevel(rule["severity"])
            assert rule["operator"] in (">", ">=", "==")
        else:
            assert rule["pass"] == 2 and SeverityLevel(rule["min_severity"]) and rule["when"]
    assert rules["R-S2-DUP"]["flags"] == ["regulatory_flag"] == rules["R-P2-INTEG"]["flags"]


def test_action_policy_file():
    policy = read_json("action_policy.json")
    types = policy["action_types"]
    expected_risk = {"rollback_release": "medium", "restart_service": "low", "scale_out": "low",
                     "throttle_traffic": "medium", "reset_consumer_offsets": "high", "remove_acl": "high",
                     "force_broker_failover": "high", "renew_certificate": "medium", "investigate_further": "low"}
    assert {k: types[k]["risk_level"] for k in expected_risk} == expected_risk
    for name, rule in types.items():
        if rule["risk_level"] == "high":
            assert rule["requires_runbook"] and not rule["can_be_first_step"] and rule["two_step_confirmation"], name
    assert {"delete_topic", "purge_data", "disable_tls"} <= set(policy["deny"])
    assert not set(policy["deny"]) & set(types)


def test_prompt_versions_file():
    agents = read_json("prompt_versions.json")["agents"]
    assert set(agents) == {"triage_agent", "rca_agent", "change_correlation_agent", "recommendation_agent"}
    for entry in agents.values():
        assert entry["active"] in entry["versions"]
        assert set(entry["versions"][entry["active"]]) >= {"guidelines", "few_shot", "created_by_adaptation"}


def test_baselines_file():
    baselines = read_json("baselines.json")
    assert baselines["baseline_minutes"] == 20 and baselines["zscore_threshold"] == 3.0
    assert {t["source"] for t in baselines["hard_thresholds"].values()} <= {s.value for s in TelemetrySource}


# --- golden dataset seed (Req. Section 12.1)

def test_golden_dataset_shape():
    """The 25-case seed is intact; promoted feedback cases (Phase 6) may follow it."""
    inputs = read_json("test_inputs.json")
    rubric = read_json("eval_rubric.json")
    cases = rubric["cases"]
    seed = [c for c in cases if c["version_added"] == "golden-v1"]
    assert len(seed) == 25
    assert Counter(c["scenario_id"] for c in seed) == {s: 5 for s in SCENARIOS}
    assert Counter(c["variant"] for c in seed) == {v: 5 for v in
                                                   ("replay", "free_text", "narrow_window", "wide_window",
                                                    "missing_source")}
    assert [c["input_id"] for c in cases] == [i["input_id"] for i in inputs]
    assert len({c["case_id"] for c in cases}) == len(cases)
    assert rubric["version"].startswith("golden-v")


@pytest.mark.parametrize("case", json.loads((DATA / "eval_rubric.json").read_text(encoding="utf-8"))["cases"],
                         ids=lambda c: c["case_id"])
def test_golden_case_is_consistent_with_data(case):
    test_input = next(i for i in json.loads((DATA / "test_inputs.json").read_text(encoding="utf-8"))
                      if i["input_id"] == case["input_id"])
    InputMode(test_input["input_mode"])
    IssueClass(case["expected_issue_class"])
    SeverityLevel(case["expected_severity"])
    if test_input["window"]:
        assert test_input["window"]["start"] < test_input["window"]["end"]
    scenario_id = case["scenario_id"]
    ids = {r["record_id"] for src in SOURCE_FILES for r in load(scenario_id, src)}
    assert set(case["must_cite_evidence_ids"]) <= ids
    withheld = set(test_input["withheld_sources"])
    assert not any(i.split("-")[0] in withheld for i in case["must_cite_evidence_ids"])
    assert case["expected_abstention"] == bool(withheld)
    if case["expected_causal_change_id"]:
        assert case["expected_causal_change_id"] in {r["record_id"] for r in load(scenario_id, "DEP")}
    assert case["expected_runbook_id"] in case["expected_doc_ids"]
    route = case["expected_dispatch"]["route"]
    assert route == ("page" if SeverityLevel(case["expected_severity"]).group.value == "major" else "assign")


def test_scenario_dates_are_utc_and_distinct():
    starts = [manifest(s)["window_start"] for s in SCENARIOS]
    assert len(set(starts)) == 5
    assert all(datetime.fromisoformat(s.replace("Z", "+00:00")).utcoffset().total_seconds() == 0 for s in starts)
    assert set(SCENARIO_DIRS) == set(ALL_DATASETS)

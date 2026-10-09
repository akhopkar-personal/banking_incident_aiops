"""Severity Rules Engine (Architecture Spec Section 5.3; Req. Section 10.6).

Deterministic and LLM-free. `compute_signals` turns a scenario's telemetry
into the signals of Section 5.3.2, each as a per-minute series per scope (a
service, route, consumer group or product). `evaluate` applies the rules in
data/severity_rules.yaml: a pass-1 rule fires when, in at least one scope, the
condition holds for `sustained_minutes` consecutive minutes; pass-2 rules
match the Triage result. The result is the highest severity fired (S4 if
none), never lower than the previous pass (raise-only).
"""

from __future__ import annotations

import operator
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from src import config
from src.logger_setup import log_interaction
from src.schemas.analysis import FiredRule, RulesResult, TriageResult
from src.schemas.enums import SeverityLevel, most_severe
from src.tools.implementations._common import Row, ToolContext, select

BASELINE_MINUTES = 20
OPERATORS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}


# -------------------------------------------------------------------- rules

@dataclass(frozen=True)
class RuleSet:
    version: str
    rules: tuple[dict[str, Any], ...]


def load_rules(path: Optional[Path] = None) -> RuleSet:
    path = Path(path) if path else config.get_settings().reference_file("severity_rules.yaml")
    return _load(str(path), path.stat().st_mtime)


@lru_cache(maxsize=4)
def _load(path: str, _mtime: float) -> RuleSet:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RuleSet(version=str(data["version"]), rules=tuple(data["rules"]))


# ------------------------------------------------------------------ signals

@dataclass
class SignalValue:
    """A signal over the window. `series` maps scope -> minute -> value. A confirmed value
    (from cited RCA evidence, used by the re-score) counts as sustained."""

    value: float = 0.0
    series: dict[str, dict[datetime, float]] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    confirmed: bool = False

    @classmethod
    def from_series(cls, series: dict[str, dict[datetime, float]], evidence: dict[tuple[str, datetime], str]
                    ) -> "SignalValue":
        best = max(((v, s, m) for s, points in series.items() for m, v in points.items()), default=(0.0, None, None))
        ids = [evidence[(best[1], best[2])]] if best[1] is not None and (best[1], best[2]) in evidence else []
        return cls(value=float(best[0]), series=series, evidence_ids=ids)


def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def _run_lengths(points: dict[datetime, float], test) -> int:
    """Longest run of consecutive minutes where test(value) is true."""
    best = run = 0
    previous = None
    for minute in sorted(points):
        if test(points[minute]):
            run = run + 1 if previous is not None and minute - previous == timedelta(minutes=1) and run else 1
        else:
            run = 0
        previous = minute
        best = max(best, run)
    return best


def _max_series(rows: Iterable[Row], scope_of, value_of) -> tuple[dict[str, dict[datetime, float]], dict]:
    series: dict[str, dict[datetime, float]] = defaultdict(dict)
    evidence: dict[tuple[str, datetime], str] = {}
    for row in rows:
        scope, minute, value = scope_of(row), _minute(row.ts), value_of(row)
        if value is None:
            continue
        if minute not in series[scope] or value > series[scope][minute]:
            series[scope][minute] = value
            evidence[(scope, minute)] = row.record_id
    return dict(series), evidence


def _baseline(points: dict[datetime, float]) -> float:
    if not points:
        return 0.0
    start = min(points)
    values = [v for m, v in points.items() if m < start + timedelta(minutes=BASELINE_MINUTES)]
    return sum(values) / len(values)


def _sustained_flags(points: dict[datetime, float], test, minutes: int) -> dict[datetime, bool]:
    """For each minute: is it inside a run of at least `minutes` consecutive minutes where test holds?"""
    flags = {m: False for m in points}
    ordered = sorted(points)
    run: list[datetime] = []
    for minute in ordered + [None]:
        if minute is not None and test(points[minute]) and (not run or minute - run[-1] == timedelta(minutes=1)):
            run.append(minute)
            continue
        if len(run) >= minutes:
            for m in run:
                flags[m] = True
        run = [minute] if minute is not None and test(points[minute]) else []
    return flags


def compute_signals(scenario_id: str, window_start: datetime, window_end: datetime,
                    withheld_sources: Iterable[str] = ()) -> dict[str, SignalValue]:
    """The Section 5.3.2 signals for one window. Withheld sources contribute nothing."""
    settings = config.get_settings()
    customer_facing = set(settings.customer_facing_services)
    ctx = ToolContext(scenario_id=scenario_id, withheld_sources=tuple(withheld_sources))

    def rows(src: str) -> list[Row]:
        return [] if src in ctx.withheld_sources else select(ctx, src, window_start, window_end)

    api, kafka, quorum, complaints = rows("API"), rows("KFK"), rows("QRM"), rows("CMP")
    signals: dict[str, SignalValue] = {}

    cf_api = [r for r in api if r.service in customer_facing]
    err, err_ev = _max_series(cf_api, lambda r: r.service, lambda r: r.data["error_rate"] * 100)
    signals["customer_error_rate_pct"] = SignalValue.from_series(err, err_ev)

    routes = [r for r in api if r.service == "payment-network-gateway"]
    route, route_ev = _max_series(routes, lambda r: r.data["endpoint"], lambda r: r.data["error_rate"] * 100)
    signals["payment_route_failure_pct"] = SignalValue.from_series(route, route_ev)

    p95, p95_ev = _max_series(cf_api, lambda r: r.service, lambda r: r.data["p95_latency_ms"])
    ratios = {s: {m: v / _baseline(points) for m, v in points.items()} for s, points in p95.items() if _baseline(points)}
    over = {s: _sustained_flags(points, lambda v: v > 3, 5) for s, points in ratios.items()}
    minutes = sorted({m for points in ratios.values() for m in points})
    signals["latency_ratio_services_over_3x"] = SignalValue.from_series(
        {"all": {m: float(sum(1 for s in over if over[s].get(m))) for m in minutes}}, {})
    signals["latency_ratio_services_over_3x"].evidence_ids = [
        p95_ev[(s, m)] for s in over for m in sorted(over[s]) if over[s][m] and (s, m) in p95_ev][:3]

    degraded_points = {s: {m: float(err.get(s, {}).get(m, 0) > 2 or ratios.get(s, {}).get(m, 0) > 2) for m in points}
                       for s, points in ratios.items()}
    sustained_degraded = {s: _sustained_flags(points, lambda v: v > 0, 5) for s, points in degraded_points.items()}
    signals["single_service_degraded"] = SignalValue.from_series(
        {"all": {m: float(any(f.get(m) for f in sustained_degraded.values())) for m in minutes}}, {})

    payments_topic = [r for r in kafka if r.data.get("topic") == "payments.transactions"]
    off, off_ev = _max_series(quorum, lambda r: "cluster", lambda r: r.data["offline_partitions"])
    signals["offline_partitions"] = SignalValue.from_series(off, off_ev)

    urp_rows = [r for r in kafka if r.service == "kafka-platform" and r.data.get("under_replicated") is not None]
    urp, urp_ev = _max_series(urp_rows, lambda r: "kafka-platform", lambda r: r.data["under_replicated"])
    signals["under_replicated_partitions"] = SignalValue.from_series(urp, urp_ev)

    lag_rows = [r for r in payments_topic if r.data.get("lag") is not None]
    lag, lag_ev = _max_series(lag_rows, lambda r: r.data.get("consumer_group", r.service), lambda r: r.data["lag"])
    signals["consumer_lag_payments"] = SignalValue.from_series(lag, lag_ev)

    produced = [r for r in payments_topic if r.data["event_type"] == "produced" and r.data.get("transaction_id")]
    counts = Counter(r.data["transaction_id"] for r in produced)
    seen: set[str] = set()
    repeats = []
    for r in produced:
        if r.data["transaction_id"] in seen:
            repeats.append(r.record_id)
        seen.add(r.data["transaction_id"])
    duplicates = sum(1 for n in counts.values() if n > 1)
    signals["duplicate_transaction_ids"] = SignalValue(
        value=float(duplicates), series={"window": {_minute(window_start): float(duplicates)}}, evidence_ids=repeats[:3])

    by_product: dict[str, list[Row]] = defaultdict(list)
    for r in complaints:
        by_product[r.data["product"]].append(r)
    rolling: dict[str, dict[datetime, float]] = {}
    rolling_ev: dict[tuple[str, datetime], str] = {}
    for product, members in by_product.items():
        points = {}
        for r in members:
            count = sum(1 for o in members if r.ts - timedelta(minutes=30) < o.ts <= r.ts)
            points[_minute(r.ts)] = max(points.get(_minute(r.ts), 0), float(count))
            rolling_ev[(product, _minute(r.ts))] = r.record_id
        rolling[product] = points
    signals["complaints_per_product_30min"] = SignalValue.from_series(rolling, rolling_ev)
    return signals


def apply_confirmed_inputs(signals: dict[str, SignalValue], inputs: dict[str, float]) -> dict[str, SignalValue]:
    """Signals for the re-score: values confirmed by cited RCA evidence count as sustained (FR-50)."""
    out = dict(signals)
    for name, value in inputs.items():
        if name in out and value > out[name].value:
            out[name] = SignalValue(value=float(value), series={"rca": {datetime.now(timezone.utc): float(value)}},
                                    confirmed=True)
    return out


# --------------------------------------------------------------- evaluation

def _fires(rule: dict[str, Any], signal: Optional[SignalValue]) -> Optional[float]:
    """The observed value if the rule fires on this signal, else None."""
    if signal is None:
        return None
    compare = OPERATORS[rule["operator"]]
    threshold = rule["threshold"]
    test = (lambda v: compare(bool(v), bool(threshold))) if isinstance(threshold, bool) else (
        lambda v: compare(v, threshold))
    needed = 1 if signal.confirmed else int(rule.get("sustained_minutes", 1))
    observed = None
    for points in signal.series.values():
        if _run_lengths(points, test) >= needed:
            peak = max(v for v in points.values() if test(v))
            observed = peak if observed is None else max(observed, peak)
    return observed


def _pass2_fires(rule: dict[str, Any], triage: TriageResult) -> bool:
    when = rule.get("when", {})
    if "issue_class" in when and triage.issue_class.value != when["issue_class"]:
        return False
    if "affected_services_any" in when:
        services = {a.service for a in triage.affected_services}
        if not services & set(when["affected_services_any"]):
            return False
    return bool(when)


def evaluate(pass_name: str, signals: dict[str, SignalValue], triage: Optional[TriageResult] = None,
             previous: Optional[RulesResult] = None, rules: Optional[RuleSet] = None,
             at: Optional[datetime] = None) -> RulesResult:
    """Apply the rules. pass1: pass-1 rules only. pass2 and rescore: pass-1 rules on the given
    signals plus pass-2 rules on the Triage result. Never below `previous` (raise-only)."""
    if pass_name not in ("pass1", "pass2", "rescore"):
        raise ValueError(f"unknown pass {pass_name!r}")
    rules = rules or load_rules()
    fired: list[FiredRule] = []
    for rule in rules.rules:
        if rule["pass"] == 1:
            observed = _fires(rule, signals.get(rule["signal"]))
            if observed is not None:
                threshold = float(rule["threshold"]) if not isinstance(rule["threshold"], bool) else 1.0
                fired.append(FiredRule(rule_id=rule["id"], observed=round(float(observed), 4), threshold=threshold,
                                       severity=SeverityLevel(rule["severity"]), flags=list(rule.get("flags", []))))
        elif pass_name != "pass1" and triage is not None and _pass2_fires(rule, triage):
            fired.append(FiredRule(rule_id=rule["id"], observed=1.0, threshold=1.0,
                                   severity=SeverityLevel(rule["min_severity"]), flags=list(rule.get("flags", []))))

    all_fired = list(previous.rules_fired) if previous else []
    known = {r.rule_id for r in all_fired}
    all_fired += [r for r in fired if r.rule_id not in known]
    level = most_severe(*(r.severity for r in all_fired if r.severity), previous.level if previous else None)
    flags = list(dict.fromkeys([*(previous.flags if previous else []), *(f for r in all_fired for f in r.flags)]))
    return RulesResult(pass_name=pass_name, level=level, group=level.group, rules_fired=all_fired, flags=flags,
                       rules_version=rules.version, evaluated_at=at or datetime.now(timezone.utc))


def would_raise(previous: RulesResult, signals: dict[str, SignalValue], inputs: dict[str, float],
                triage: Optional[TriageResult] = None) -> bool:
    """Whether RCA-confirmed inputs would raise the severity: the condition for the one re-score (FR-50)."""
    if not inputs:
        return False
    rescored = evaluate("rescore", apply_confirmed_inputs(signals, inputs), triage, previous)
    return rescored.level.is_more_severe_than(previous.level)


def log_result(result: RulesResult, component: str) -> None:
    log_interaction("severity_rules_evaluated", component=component, pass_name=result.pass_name,
                    severity=result.level.value, group=result.group.value,
                    rules_fired=[r.rule_id for r in result.rules_fired], flags=result.flags,
                    rules_version=result.rules_version)

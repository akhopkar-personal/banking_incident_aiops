"""Adaptation engine (Architecture Spec Section 11.3; Req. FR-31 to FR-34, FR-62, Sections 12.5, 12.6).

    scan_signals -> group_patterns -> propose (status proposed, or waiting below the threshold)
    -> SME / Evaluation Engineer approves or rejects
    -> apply: golden set before, new prompt version (inactive), golden set after with the new
       version as an override, regression check, and for preference-driven proposals a held-out
       pairwise session -> the new version goes live (applied) or the old one stays (reverted).

Changes are limited to a prompt guideline, a few-shot example or a retrieval alias
(`check_adaptation_scope`); severity rules are never changed here: severity signals
go to `rules_review_summary` for the SME. Entries live in data/feedback/adaptation_log.json.
"""

from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from src import config
from src.logger_setup import log_error, log_eval, log_interaction
from src.safety.guardrails import check_adaptation_scope
from src.schemas.enums import ReviewerRole, SignalType
from src.schemas.feedback import AdaptationLogEntry, MetricSnapshot

from . import deepeval_harness

APPROVER_ROLES = (ReviewerRole.SME, ReviewerRole.EVALUATION_ENGINEER)
ISSUE_TARGETS = {  # issue type -> (agent or "retrieval", change type)
    "triage_class_error": ("triage_agent", "prompt_rule"),
    "root_cause_error": ("rca_agent", "prompt_rule"),
    "missing_evidence": ("rca_agent", "prompt_rule"),
    "change_correlation_miss": ("change_correlation_agent", "prompt_rule"),
    "citation_error": ("recommendation_agent", "prompt_rule"),
    "verbosity": ("recommendation_agent", "prompt_rule"),
    "retrieval_miss": ("recommendation_agent", "retrieval_alias"),
}
RATING_AGENTS = {"rca": "rca_agent", "actions": "recommendation_agent", "summary": "recommendation_agent"}
METRIC_AGENTS = {"triage_class_match": "triage_agent", "rca_top1": "rca_agent", "rca_rubric_top1": "rca_agent",
                 "change_hit": "change_correlation_agent", "red_herring_rejected": "change_correlation_agent",
                 "retrieval_recall_at_5": "recommendation_agent", "citation_validity": "recommendation_agent"}
AGENT_METRIC = {"triage_agent": "triage_class_match", "rca_agent": "rca_top1",
                "change_correlation_agent": "change_hit", "recommendation_agent": "citation_validity"}
_lock = threading.RLock()
Progress = Callable[[str], None]


def now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ storage


def _log_path() -> Path:
    return config.get_settings().feedback_dir / "adaptation_log.json"


def entries() -> list[AdaptationLogEntry]:
    path = _log_path()
    if not path.exists():
        return []
    with _lock:
        data = json.loads(path.read_text(encoding="utf-8"))
    return [AdaptationLogEntry.model_validate(e) for e in data.get("entries", [])]


def get(adaptation_id: str) -> AdaptationLogEntry:
    found = [e for e in entries() if e.adaptation_id == adaptation_id]
    if not found:
        raise ValueError(f"unknown adaptation {adaptation_id}")
    return found[0]


def _store(entry: AdaptationLogEntry) -> AdaptationLogEntry:
    entry = AdaptationLogEntry.model_validate(entry.model_dump())  # re-run the status and scope checks
    with _lock:
        items = entries()
        ids = [e.adaptation_id for e in items]
        if entry.adaptation_id in ids:
            items[ids.index(entry.adaptation_id)] = entry
        else:
            items.append(entry)
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "entries": [e.model_dump(mode="json") for e in items]},
                                   indent=1), encoding="utf-8")
    return entry


def _new_id() -> str:
    used = {e.adaptation_id for e in entries()}
    n = len(used) + 1
    while f"ADP-{n:03d}" in used:
        n += 1
    return f"ADP-{n:03d}"


# ------------------------------------------------------------------ signals


def scan_signals(since: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Explicit (promoted candidates), preference (low ratings, pairwise losses, preferred outputs) and
    implicit (golden cases failing the same metric in the last two runs) signals."""
    from src.feedback import feedback_loop, pairwise_session, preference_store

    reviews = {r.feedback_id: r for r in preference_store.query(SignalType.REVIEW)}
    signals: list[dict[str, Any]] = []
    for c in feedback_loop.candidates():
        if c.status != "promoted" or c.issue_type is None or (since and c.created_at < since):
            continue
        review = reviews.get(c.feedback_id)
        signals.append({"source": "feedback", "kind": c.issue_type.value, "issue_type": c.issue_type.value,
                        "scenario_id": c.original_incident.get("scenario_id"), "feedback_id": c.feedback_id,
                        "reviewer_id": review.reviewer_id if review else "unknown", "reason": c.reviewer_reason,
                        "run_id": c.run_id})
    for r in preference_store.query(SignalType.RATING, since=since):
        for dimension, agent in RATING_AGENTS.items():
            if r.payload.get(dimension, 5) <= 2:
                signals.append({"source": "preference", "kind": f"low_rating_{dimension}", "agent": agent,
                                "scenario_id": r.scenario_id, "feedback_id": r.feedback_id,
                                "reviewer_id": r.reviewer_id, "reason": f"{dimension} rated {r.payload[dimension]}",
                                "run_id": r.run_id})
    for session in pairwise_session.sessions():
        for p in session["pairs"]:
            if p["held_out"]:
                continue  # held-out pairs only measure the win rate (Section 10.5)
            status, final = preference_store.pair_outcome(p["pair_id"])
            if status not in ("agreed", "tie_broken") or final not in ("A", "B"):
                continue
            labels = preference_store.pair_labels(p["pair_id"])
            loser = "B" if final == "A" else "A"
            base = {"source": "preference", "agent": session["agent"], "scenario_id": p["scenario_id"],
                    "feedback_id": labels[0].feedback_id, "reviewer_id": labels[0].reviewer_id,
                    "reason": "; ".join(r.payload.get("reason", "") for r in labels[:2])}
            signals.append({**base, "kind": "pairwise_loss", "run_id": p[f"run_{loser.lower()}"]["run_id"],
                            "version": session[f"version_{loser.lower()}"]})
            signals.append({**base, "kind": "preferred_output", "run_id": p[f"run_{final.lower()}"]["run_id"],
                            "version": session[f"version_{final.lower()}"]})
    golden_runs = deepeval_harness.saved_results("golden")
    if len(golden_runs) >= 2:
        latest, previous = golden_runs[0], golden_runs[1]
        before = {c["case_id"]: c["metrics"] for c in previous["cases"]}
        for case in latest["cases"]:
            for metric, agent in METRIC_AGENTS.items():
                if case["metrics"].get(metric) == 0.0 and before.get(case["case_id"], {}).get(metric) == 0.0:
                    signals.append({"source": "implicit", "kind": f"eval_failure_{metric}", "agent": agent,
                                    "scenario_id": case["scenario_id"], "feedback_id": None, "reviewer_id": "system",
                                    "reason": f"{case['case_id']} failed {metric} in two consecutive runs",
                                    "run_id": case.get("run_id"), "metric": metric})
    return signals


def _target(signal: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """(agent, change type) for a signal, or (None, None) when it goes to the SME's rules review."""
    if signal.get("issue_type"):
        return ISSUE_TARGETS.get(signal["issue_type"], (None, None))
    if signal["kind"] == "preferred_output":
        return signal["agent"], "few_shot_example"
    if signal["kind"] == "eval_failure_retrieval_recall_at_5":
        return signal["agent"], "retrieval_alias"
    return signal.get("agent"), "prompt_rule"


def _family(scenario_id: Optional[str]) -> str:
    return scenario_id or "any"


def group_patterns(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by (signal kind, target agent, scenario family) and apply the occurrence threshold and the
    reviewer cap: at most 30% of a pattern's human signals from one reviewer, and never more than one
    from one reviewer while the pattern is too small for 30% to allow more."""
    settings = config.get_settings()
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in signals:
        agent, change_type = _target(s)
        if agent is None:
            continue
        groups[(s["kind"], agent, change_type, _family(s.get("scenario_id")))].append(s)
    patterns = []
    for (kind, agent, change_type, family), items in groups.items():
        human = [s["reviewer_id"] for s in items if s["source"] != "implicit"]
        per_reviewer = Counter(human)
        cap = max(1, int(settings.max_reviewer_share * len(human)))
        capped = bool(per_reviewer) and max(per_reviewer.values()) > cap
        eligible = len(items) >= settings.adaptation_min_occurrences and not capped
        patterns.append({
            "key": f"{kind}|{agent}|{family}", "kind": kind, "agent": agent, "change_type": change_type,
            "scenario_id": family, "count": len(items), "threshold": settings.adaptation_min_occurrences,
            "reviewers": dict(per_reviewer), "reviewer_cap_exceeded": capped, "eligible": eligible,
            "source": "preference" if items[0]["source"] == "preference" else items[0]["source"],
            "feedback_ids": [s["feedback_id"] for s in items if s.get("feedback_id")],
            "reasons": [s["reason"] for s in items if s.get("reason")][:8],
            "run_ids": [s["run_id"] for s in items if s.get("run_id")], "signals": items,
        })
    return sorted(patterns, key=lambda p: (not p["eligible"], -p["count"]))


# ---------------------------------------------------------------- proposals


class GuidelineDraft(BaseModel):
    guideline: str = Field(description="One imperative sentence for the agent's prompt, at most 60 words")


def draft_guideline(pattern: dict[str, Any], use_llm: bool = True) -> str:
    """One guideline sentence from the reviewers' reasons: drafted by the LLM, else from a template.
    The approver can edit it before approving."""
    reasons = "; ".join(pattern["reasons"])[:600]
    fallback = (f"Reviewers flagged {pattern['kind'].replace('_', ' ')} in {pattern['count']} "
                f"{pattern['scenario_id']} incidents ({reasons[:200]}). Check for this before you answer.")
    if not use_llm or not config.get_settings().llm_enabled:
        return fallback
    try:
        from src.agent import llm

        model = llm.chat_model().with_structured_output(GuidelineDraft, method="function_calling")
        draft = model.invoke(
            f"You improve the prompt of the {pattern['agent']} in a banking incident-analysis system. Human "
            f"reviewers reported this problem {pattern['count']} times ({pattern['kind']}). Their reasons: "
            f"{reasons}\nWrite ONE general rule the agent should follow to avoid it. Do not mention specific "
            f"incident IDs, evidence IDs or dates.")
        return draft.guideline.strip() or fallback
    except Exception as exc:  # noqa: BLE001 - the template is good enough to start from
        log_error("system_error", component="adaptation_engine", stage="draft_guideline", error=type(exc).__name__,
                  incident_id="n/a", run_id="n/a")
        return fallback


def _few_shot_example(pattern: dict[str, Any]) -> dict[str, Any]:
    from src.feedback.pairwise_session import agent_part
    from src.services import incident_state_store as store

    run_id = pattern["run_ids"][0]
    incident = store.load_incident(run_id) or {}
    return {"input": {k: incident.get(k) for k in ("scenario_id", "input_mode", "raw_input", "reported_service")},
            "output": agent_part(store.load_output(run_id), pattern["agent"]), "source_run_id": run_id}


def propose_change(pattern: dict[str, Any], use_llm: bool = True) -> dict[str, Any]:
    agent, change_type = pattern["agent"], pattern["change_type"]
    if change_type == "prompt_rule":
        change = {"type": "prompt_rule", "agent": agent, "text": draft_guideline(pattern, use_llm),
                  "target_file": "data/prompt_versions.json"}
    elif change_type == "few_shot_example":
        change = {"type": "few_shot_example", "agent": agent, "example": _few_shot_example(pattern),
                  "target_file": "data/prompt_versions.json"}
    else:
        from src.evaluation import golden_dataset

        expected = next((c["expected_runbook_id"] for c in golden_dataset.cases()
                         if c["scenario_id"] == pattern["scenario_id"]), None)
        change = {"type": "retrieval_alias", "agent": agent, "alias_key": pattern["scenario_id"],
                  "terms": [expected] if expected else [], "target_file": "knowledge/retrieval_aliases.json"}
    change["target_metric"] = next((s["metric"] for s in pattern["signals"] if s.get("metric")),
                                   AGENT_METRIC.get(agent, "rca_top1"))
    allowed, why = check_adaptation_scope(change)
    if not allowed:
        raise ValueError(f"proposal outside the adaptation scope: {why}")
    return change


def scan_and_propose(reviewer_role: ReviewerRole, since: Optional[datetime] = None) -> list[AdaptationLogEntry]:
    """Scan, group, and record every pattern: `proposed` if eligible, else `waiting`. A pattern that
    already has an open entry (waiting, proposed or approved) updates that entry instead."""
    if reviewer_role not in APPROVER_ROLES:
        raise ValueError("the SME or the Evaluation Engineer runs the adaptation scan")
    open_by_key = {e.trigger_pattern.get("key"): e for e in entries() if e.status in ("waiting", "proposed",
                                                                                     "approved")}
    recorded = []
    for pattern in group_patterns(scan_signals(since)):
        existing = open_by_key.get(pattern["key"])
        if existing is not None and existing.status == "approved":
            continue
        status = "proposed" if pattern["eligible"] else "waiting"
        if existing is not None and existing.status == status and existing.trigger_pattern.get("count") == \
                pattern["count"]:
            continue
        trigger = {k: v for k, v in pattern.items() if k != "signals"}
        keep = (existing is not None and existing.status == status
                and existing.proposed_change.get("type") == pattern["change_type"])
        change = existing.proposed_change if keep else propose_change(pattern, use_llm=status == "proposed")
        entry = AdaptationLogEntry(
            adaptation_id=existing.adaptation_id if existing else _new_id(), status=status, source=pattern["source"],
            trigger_pattern=trigger, proposed_change=change, feedback_ids=pattern["feedback_ids"],
            explanation=(f"{pattern['count']} {pattern['source']} signals ({pattern['kind'].replace('_', ' ')}) "
                         f"for {pattern['agent']} in {pattern['scenario_id']} incidents; threshold "
                         f"{pattern['threshold']}."), created_at=existing.created_at if existing else now())
        entry = _store(entry)
        recorded.append(entry)
        if status == "proposed":
            present_for_approval(entry)
    return recorded


def present_for_approval(entry: AdaptationLogEntry) -> None:
    pattern = entry.trigger_pattern
    log_interaction("adaptation_proposed", component="adaptation_engine", adaptation_id=entry.adaptation_id,
                    pattern=pattern.get("key"), feedback_ids=entry.feedback_ids, count=pattern.get("count"),
                    threshold=pattern.get("threshold"), target_agent=entry.proposed_change.get("agent"),
                    change_type=entry.proposed_change.get("type"), source=entry.source)


def approve(adaptation_id: str, reviewer_role: ReviewerRole, reviewer_id: str,
            edited_change: Optional[dict[str, Any]] = None) -> AdaptationLogEntry:
    """H-10: the approver may edit the proposed text before approving."""
    if reviewer_role not in APPROVER_ROLES:
        raise ValueError("only the SME or the Evaluation Engineer approves an adaptation (FR-32)")
    entry = get(adaptation_id)
    if entry.status != "proposed":
        raise ValueError(f"{adaptation_id} is {entry.status}, not proposed")
    change = {**entry.proposed_change, **(edited_change or {})}
    allowed, why = check_adaptation_scope(change)
    if not allowed:
        raise ValueError(f"the edited change is outside the adaptation scope: {why}")
    entry = _store(entry.model_copy(update={"status": "approved", "approved_by": reviewer_role,
                                            "proposed_change": change, "decided_at": now()}))
    log_interaction("adaptation_approved", component="adaptation_engine", adaptation_id=adaptation_id,
                    reviewer_role=reviewer_role.value, reviewer_id=reviewer_id)
    return entry


def reject(adaptation_id: str, reason: str, reviewer_role: ReviewerRole, reviewer_id: str) -> AdaptationLogEntry:
    if reviewer_role not in APPROVER_ROLES:
        raise ValueError("only the SME or the Evaluation Engineer rejects an adaptation (FR-32)")
    if not reason.strip():
        raise ValueError("a reason is required to reject an adaptation")
    entry = get(adaptation_id)
    if entry.status not in ("proposed", "waiting"):
        raise ValueError(f"{adaptation_id} is {entry.status} and cannot be rejected")
    entry = _store(entry.model_copy(update={"status": "rejected", "reject_reason": reason.strip(),
                                            "decided_at": now()}))
    log_interaction("adaptation_rejected", component="adaptation_engine", adaptation_id=adaptation_id,
                    reason=reason.strip(), reviewer_role=reviewer_role.value, reviewer_id=reviewer_id)
    return entry


# -------------------------------------------------------------------- apply


def _versions_file() -> Path:
    return config.get_settings().prompt_versions_path


def _write_versions(data: dict[str, Any]) -> None:
    path = _versions_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _add_version(agent: str, change: dict[str, Any], adaptation_id: str) -> tuple[str, str]:
    """Write a new, inactive prompt version built on the active one. Returns (before, after)."""
    with _lock:
        data = json.loads(_versions_file().read_text(encoding="utf-8"))
        entry = data["agents"][agent]
        before = entry["active"]
        base = entry["versions"][before]
        numbers = [int(v[1:]) for v in entry["versions"] if v[1:].isdigit()]
        after = f"v{max(numbers) + 1}"
        new = {"guidelines": list(base.get("guidelines", [])), "few_shot": list(base.get("few_shot", [])),
               "created_by_adaptation": adaptation_id, "created_at": now().isoformat()}
        if change["type"] == "prompt_rule":
            new["guidelines"].append(change["text"])
        else:
            new["few_shot"].append(change["example"])
        entry["versions"][after] = new
        _write_versions(data)
    return before, after


def _set_active(agent: str, version: str) -> None:
    with _lock:
        data = json.loads(_versions_file().read_text(encoding="utf-8"))
        data["agents"][agent]["active"] = version
        _write_versions(data)


def _aliases(update: Callable[[dict[str, list[str]]], None]) -> None:
    path = config.get_settings().retrieval_aliases_path
    with _lock:
        data = json.loads(path.read_text(encoding="utf-8"))
        update(data["aliases"])
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _snapshots(aggregate: dict[str, Any]) -> list[MetricSnapshot]:
    names = [*deepeval_harness.TRACKED, "hallucination_rate"]
    return [MetricSnapshot(metric_name=n, value=float(aggregate[n])) for n in names if aggregate.get(n) is not None]


def regression_check(before: dict[str, Any], after: dict[str, Any], target: str) -> dict[str, Any]:
    """No tracked metric may drop by more than `regression_tolerance_points`; the target must improve."""
    tolerance = config.get_settings().regression_tolerance_points / 100
    deltas = {m: round(after[m] - before[m], 4) for m in deepeval_harness.TRACKED
              if before.get(m) is not None and after.get(m) is not None}
    worst = min(deltas.values()) if deltas else 0.0
    regressed = [m for m, d in deltas.items() if d < -tolerance]
    target_delta = deltas.get(target)
    improved = target_delta > 0 if target_delta is not None else sum(deltas.values()) > 0
    return {"deltas": deltas, "max_regression_points": round(worst * 100, 1), "regressed": regressed,
            "target_metric": target, "target_delta": target_delta, "improved": improved,
            "passed": not regressed and improved}


def apply(adaptation_id: str, *, use_judge: bool = True, case_ids: Optional[list[str]] = None,
          progress: Optional[Progress] = None, judge: Any = None) -> AdaptationLogEntry:
    """Section 11.3 steps 1 to 7. A preference-driven proposal that passes the golden set waits for its
    held-out pairwise session (`finish_preference_check`)."""
    entry = get(adaptation_id)
    if entry.status != "approved":
        raise ValueError(f"{adaptation_id} must be approved first (it is {entry.status})")
    change = entry.proposed_change
    agent, target = change.get("agent"), change.get("target_metric", "rca_top1")

    def say(message: str) -> None:
        if progress:
            progress(message)

    def golden(phase: str, override: Optional[dict[str, str]]) -> dict[str, Any]:
        return deepeval_harness.run_golden_set(
            override, phase=phase, adaptation_id=adaptation_id, case_ids=case_ids, use_judge=use_judge, judge=judge,
            progress=lambda done, total, case: say(f"{phase}: {done}/{total} cases ({case})"))

    say("Measuring the current versions on the golden set")
    before = golden("before", None)
    override: Optional[dict[str, str]] = None
    if change["type"] == "retrieval_alias":
        version_before = version_after = None
        _aliases(lambda a: a.setdefault(change["alias_key"], []).extend(
            t for t in change["terms"] if t not in a.get(change["alias_key"], [])))
    else:
        version_before, version_after = _add_version(agent, change, adaptation_id)
        override = {agent: version_after}
    say("Measuring the new version on the golden set")
    after = golden("after", override)
    check = regression_check(before["aggregate"], after["aggregate"], target)
    trigger = {**entry.trigger_pattern, "before_eval_id": before["eval_id"], "after_eval_id": after["eval_id"],
               "regression_check": check}
    entry = _store(entry.model_copy(update={
        "trigger_pattern": trigger, "before_metrics": _snapshots(before["aggregate"]),
        "after_metrics": _snapshots(after["aggregate"]), "regression_check_passed": check["passed"],
        "prompt_version_before": version_before, "prompt_version_after": version_after}))
    log_eval("eval_result", component="adaptation_engine", adaptation_id=adaptation_id, phase="regression_check",
             before_eval_id=before["eval_id"], after_eval_id=after["eval_id"],
             golden_dataset_version=after["golden_dataset_version"],
             regression_check="pass" if check["passed"] else "fail", deltas=check["deltas"],
             max_regression_points=check["max_regression_points"], target_metric=target,
             target_delta=check["target_delta"])
    if check["passed"] and entry.source == "preference" and change["type"] != "retrieval_alias":
        from src.feedback import pairwise_session
        from src.evaluation import golden_dataset

        say("Golden set passed; creating the held-out pairwise session")
        cases = [c["case_id"] for c in golden_dataset.cases() if c["scenario_id"] == entry.trigger_pattern.get(
            "scenario_id")] or [c["case_id"] for c in golden_dataset.cases()][:4]
        session = pairwise_session.create_session(agent, version_before, version_after, cases, held_out_share=1.0,
                                                  adaptation_id=adaptation_id)
        trigger = {**entry.trigger_pattern, "pairwise_session_id": session["session_id"]}
        return _store(entry.model_copy(update={"trigger_pattern": trigger}))
    return _finish(entry, check["passed"], _reason(check))


def _reason(check: dict[str, Any]) -> str:
    if check["regressed"]:
        return f"regression in {', '.join(check['regressed'])} (worst {check['max_regression_points']} points)"
    if not check["improved"]:
        return f"{check['target_metric']} did not improve"
    return "target improved with no regression"


def finish_preference_check(adaptation_id: str) -> AdaptationLogEntry:
    """Step 5 for preference-driven proposals: held-out win rate and the length-bias check."""
    from src.feedback import pairwise_session

    settings = config.get_settings()
    entry = get(adaptation_id)
    session_id = entry.trigger_pattern.get("pairwise_session_id")
    if entry.status != "approved" or not session_id:
        raise ValueError(f"{adaptation_id} is not waiting for a pairwise session")
    outcome = pairwise_session.summarize(session_id)
    win_rate = outcome["win_rate_b"]
    if win_rate is None:
        raise ValueError(f"no decided held-out pairs in {session_id} yet: reviewers still need to label them")
    length = pairwise_session.length_change_pct(session_id)
    length_ok = length is None or length <= settings.length_bias_max_pct or win_rate >= settings.win_rate_threshold
    passed = win_rate >= settings.win_rate_threshold and length_ok
    entry = _store(entry.model_copy(update={"held_out_win_rate": win_rate, "length_change_pct": length}))
    reason = (f"held-out win rate {win_rate:.2f} (threshold {settings.win_rate_threshold}), "
              f"length change {length if length is not None else 'n/a'}%")
    return _finish(entry, passed, reason)


def _finish(entry: AdaptationLogEntry, passed: bool, reason: str) -> AdaptationLogEntry:
    """Step 6 and 7: make the new version live, or keep the old one; write the explanation."""
    change = entry.proposed_change
    agent = change.get("agent")
    before = {m.metric_name: m.value for m in entry.before_metrics}
    after = {m.metric_name: m.value for m in entry.after_metrics}
    target = change.get("target_metric", "rca_top1")
    pattern = entry.trigger_pattern
    what = ("added a guideline to" if change["type"] == "prompt_rule" else
            "added a few-shot example to" if change["type"] == "few_shot_example" else "added a retrieval alias for")
    numbers = (f"{target} moved from {before.get(target, 'n/a')} to {after.get(target, 'n/a')}; largest drop in "
               f"another metric {pattern.get('regression_check', {}).get('max_regression_points', 'n/a')} points")
    if entry.held_out_win_rate is not None:
        numbers += f"; reviewers preferred the new version in {entry.held_out_win_rate:.0%} of held-out pairs"
    explanation = (f"{pattern.get('count')} {entry.source} signals ({str(pattern.get('kind', '')).replace('_', ' ')}) "
                   f"in {pattern.get('scenario_id')} incidents. The engine {what} {agent}"
                   + (f" ({entry.prompt_version_before} to {entry.prompt_version_after})"
                      if entry.prompt_version_after else "") + f". On the golden set {numbers}. ")
    if passed:
        if entry.prompt_version_after:
            _set_active(agent, entry.prompt_version_after)
        entry = _store(entry.model_copy(update={"status": "applied", "explanation": explanation + "Kept.",
                                                "decided_at": now()}))
        log_interaction("adaptation_applied", component="adaptation_engine", adaptation_id=entry.adaptation_id,
                        prompt_version_before={agent: entry.prompt_version_before},
                        prompt_version_after={agent: entry.prompt_version_after}, reason=reason)
    else:
        if change["type"] == "retrieval_alias":
            _aliases(lambda a: a.pop(change["alias_key"], None))
        entry = _store(entry.model_copy(update={"status": "reverted",
                                                "explanation": explanation + f"Reverted: {reason}.",
                                                "decided_at": now()}))
        log_interaction("adaptation_reverted", component="adaptation_engine", adaptation_id=entry.adaptation_id,
                        reason=reason, prompt_version_kept={agent: entry.prompt_version_before})
    return entry


# -------------------------------------------------------------- rules review


def rules_review_summary() -> list[dict[str, Any]]:
    """Severity signals for the SME (never adapted automatically): re-classifications and severity-error
    reviews per scenario, plus Triage/rules disagreements from the logs."""
    from src.feedback import feedback_loop, preference_store

    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, scenario: Optional[str], detail: str) -> None:
        row = rows.setdefault((kind, scenario or "any"), {"signal": kind, "scenario_id": scenario or "any",
                                                          "count": 0, "examples": []})
        row["count"] += 1
        if len(row["examples"]) < 3:
            row["examples"].append(detail)

    for r in preference_store.query(SignalType.RECLASSIFICATION):
        add("reclassification", r.scenario_id, f"{r.payload.get('from_level')} to {r.payload.get('to_level')}: "
                                               f"{r.payload.get('reason', '')}")
    for c in feedback_loop.candidates():
        if c.issue_type is not None and c.issue_type.value == "severity_error":
            add("severity_error", c.original_incident.get("scenario_id"), c.reviewer_reason)
    log = config.get_settings().logs_dir / "interactions.log"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if '"severity_disagreement"' in line:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                add(f"severity_disagreement {','.join(event.get('rule_ids') or []) or 'no rule'}",
                    event.get("scenario_id"),
                    f"Triage proposed {event.get('llm_proposed_level')}, rules decided {event.get('rules_level')}")
    return sorted(rows.values(), key=lambda r: -r["count"])


def rules_change_log() -> list[dict[str, Any]]:
    path = config.get_settings().feedback_dir / "rules_change_log.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("entries", data) if isinstance(data, dict) else data

"""Pairwise comparison sessions (Architecture Spec Section 10.5; Req. FR-59, FR-64, Section 12.6).

The Evaluation Engineer compares two prompt versions of one agent on golden
cases. Each case is run in `evaluation` mode once per version (nothing is
dispatched). Reviewers see the two outputs side by side in random order with
the versions hidden, and pick the better one. Each pair needs two reviewers;
a disagreement goes to the SME. Held-out pairs are never used to propose an
adaptation; they measure the win rate of version B over version A.

Sessions are stored in data/feedback/pairwise/<session_id>.json; labels are
`pairwise` records in the preference store.
"""

from __future__ import annotations

import json
import math
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from src import config
from src.logger_setup import log_eval, log_interaction
from src.schemas.enums import ReviewerRole, RunMode, SignalType
from src.schemas.feedback import PairwisePayload, PreferenceIntegrity, PreferenceRecord
from src.services import incident_state_store as store
from src.tools.implementations._common import parse_time

from . import preference_store

# The part of an investigation output each agent is responsible for (what reviewers compare).
AGENT_FIELDS = {
    "triage_agent": ("issue_class", "severity", "affected_services"),
    "rca_agent": ("hypotheses", "timeline"),
    "change_correlation_agent": ("change_findings",),
    "recommendation_agent": ("recommended_actions", "stakeholder_summary", "regulatory_notes",
                             "insufficient_evidence_reason"),
}
CHOICES = ("A", "B", "tie")
_lock = threading.RLock()
Progress = Callable[[int, int, str], None]


def _dir() -> Path:
    return config.get_settings().feedback_dir / "pairwise"


def _path(session_id: str) -> Path:
    return _dir() / f"{session_id}.json"


def load(session_id: str) -> dict[str, Any]:
    path = _path(session_id)
    if not path.exists():
        raise ValueError(f"unknown pairwise session {session_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(session: dict[str, Any]) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    _path(session["session_id"]).write_text(json.dumps(session, indent=1, default=str), encoding="utf-8")


def sessions() -> list[dict[str, Any]]:
    if not _dir().exists():
        return []
    found = [json.loads(p.read_text(encoding="utf-8")) for p in _dir().glob("PS-*.json")]
    return sorted(found, key=lambda s: s["created_at"], reverse=True)


def _new_id() -> str:
    with _lock:
        used = {s["session_id"] for s in sessions()}
        n = len(used) + 1
        while f"PS-{n:04d}" in used:
            n += 1
        return f"PS-{n:04d}"


def agent_part(output: Optional[dict[str, Any]], agent: str) -> dict[str, Any]:
    """The fields of an output that `agent` produced (empty for a SYSTEM_ERROR output)."""
    output = output or {}
    return {k: output[k] for k in AGENT_FIELDS[agent] if output.get(k) not in (None, [], "")}


def _run(case: dict[str, Any], agent: str, version: str) -> dict[str, Any]:
    from src.agent.core_agent import IntakeRejection, run_investigation

    test_input = case["input"]
    output = run_investigation(
        test_input["raw_input"], scenario_id=case["scenario_id"], supplied_window=test_input.get("window"),
        run_mode=RunMode.EVALUATION, prompt_version_override={agent: version},
        reference_time=parse_time(test_input["reference_time"]) if test_input.get("reference_time") else None,
        withheld_sources=test_input.get("withheld_sources") or [])
    if isinstance(output, IntakeRejection):
        raise ValueError(f"{case['case_id']}: input rejected ({output.reason})")
    meta = output.metadata
    return {"run_id": output.run_id, "incident_id": output.incident_id, "status": output.status.value,
            "trace_id": meta.langfuse_trace_id, "version_set": dict(meta.prompt_version_set)}


def create_session(agent: str, version_a: str, version_b: str, case_ids: list[str], *, samples: int = 1,
                   held_out_share: Optional[float] = None, adaptation_id: Optional[str] = None,
                   created_by: ReviewerRole = ReviewerRole.EVALUATION_ENGINEER, created_by_id: str = "",
                   progress: Optional[Progress] = None, concurrency: Optional[int] = None) -> dict[str, Any]:
    """Run every case under both versions and store the blind pairs (Section 10.5 "Create session")."""
    from src.agent import prompts
    from src.evaluation import golden_dataset

    if agent not in AGENT_FIELDS:
        raise ValueError(f"unknown agent {agent!r}")
    for version in (version_a, version_b):
        prompts.resolve_version(agent, {agent: version})
    if created_by not in (ReviewerRole.EVALUATION_ENGINEER, ReviewerRole.SME):
        raise ValueError("pairwise sessions are created by the Evaluation Engineer or the SME")
    cases = golden_dataset.cases(case_ids)
    if not cases:
        raise ValueError("choose at least one golden case")
    share = config.get_settings().pairwise_held_out_share if held_out_share is None else held_out_share
    session_id = _new_id()
    rng = random.Random(session_id)
    order = [c["case_id"] for c in cases]
    rng.shuffle(order)
    held_out = set(order[:math.ceil(share * len(order))])
    session = {"session_id": session_id, "agent": agent, "version_a": version_a, "version_b": version_b,
               "adaptation_id": adaptation_id, "case_ids": [c["case_id"] for c in cases], "samples": samples,
               "held_out_share": share, "created_by": created_by.value, "created_by_id": created_by_id,
               "created_at": datetime.now(timezone.utc).isoformat(), "status": "running", "pairs": [],
               "errors": []}
    _save(session)
    jobs = [(c, s) for c in cases for s in range(samples)]
    pairs: list[dict[str, Any]] = []
    done = 0

    def one(job: tuple[dict[str, Any], int]) -> dict[str, Any]:
        case, sample = job
        return {"case": case, "sample": sample, "a": _run(case, agent, version_a), "b": _run(case, agent, version_b)}

    workers = concurrency or config.get_settings().eval_concurrency
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pairwise") as pool:
        for job, future in [(j, pool.submit(one, j)) for j in jobs]:
            case, sample = job
            try:
                result = future.result()
                n = len(pairs) + 1
                pairs.append({"pair_id": f"{session_id}-P{n:02d}", "case_id": case["case_id"],
                              "scenario_id": case["scenario_id"], "sample": sample,
                              "run_a": result["a"], "run_b": result["b"],
                              "shown_order": rng.choice(["AB", "BA"]), "held_out": case["case_id"] in held_out})
            except Exception as exc:  # noqa: BLE001 - a failed case is reported, the session continues
                session["errors"].append(f"{case['case_id']}: {type(exc).__name__}: {exc}"[:300])
            done += 1
            if progress:
                progress(done, len(jobs), case["case_id"])
    session.update(pairs=pairs, status="ready" if pairs else "failed")
    _save(session)
    log_interaction("pairwise_label_recorded", component="pairwise_session", stage="session_created",
                    session_id=session_id, agent=agent, versions_compared=[f"{agent}:{version_a}",
                                                                           f"{agent}:{version_b}"],
                    pairs=len(pairs), held_out_pairs=sum(p["held_out"] for p in pairs), adaptation_id=adaptation_id)
    return session


def pair(session: dict[str, Any], pair_id: str) -> dict[str, Any]:
    found = [p for p in session["pairs"] if p["pair_id"] == pair_id]
    if not found:
        raise ValueError(f"unknown pair {pair_id}")
    return found[0]


def shown(session: dict[str, Any], pair_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(left, right) outputs as the reviewer sees them: only the compared agent's part, versions hidden."""
    p = pair(session, pair_id)
    outputs = {k: agent_part(store.load_output(p[f"run_{k.lower()}"]["run_id"]), session["agent"]) for k in "AB"}
    first, second = p["shown_order"]
    return outputs[first], outputs[second]


def next_pair_for(session: dict[str, Any], reviewer_id: str, reviewer_role: ReviewerRole) -> Optional[str]:
    """The next pair this reviewer should label: unlabelled by them and still needing a label. The SME
    gets the disputed pairs."""
    for p in session["pairs"]:
        labels = preference_store.pair_labels(p["pair_id"])
        if any(r.reviewer_id == reviewer_id for r in labels):
            continue
        status = preference_store.agreement(p["pair_id"])
        if reviewer_role == ReviewerRole.SME and status == "disputed":
            return p["pair_id"]
        if status in ("none", "single"):
            return p["pair_id"]
    return None


def label(session_id: str, pair_id: str, shown_choice: str, reason: str, reviewer_role: ReviewerRole,
          reviewer_id: str, at: Optional[datetime] = None) -> PreferenceRecord:
    """Record one blind label. `shown_choice` is "left", "right" or "tie" as the reviewer saw it."""
    from src.evaluation import langfuse_tracker

    session = load(session_id)
    p = pair(session, pair_id)
    if shown_choice not in ("left", "right", "tie"):
        raise ValueError("choose left, right or tie")
    if not reason.strip():
        raise ValueError("a reason is required for a pairwise label")
    labels = preference_store.pair_labels(pair_id)
    if any(r.reviewer_id == reviewer_id for r in labels):
        raise ValueError(f"{reviewer_id} has already labelled {pair_id}")
    status = preference_store.agreement(pair_id)
    if status in ("agreed", "tie_broken"):
        raise ValueError(f"{pair_id} is already decided")
    if status == "disputed" and reviewer_role != ReviewerRole.SME:
        raise ValueError(f"{pair_id} is disputed: only the SME can break the tie")
    choice = "tie" if shown_choice == "tie" else p["shown_order"][0 if shown_choice == "left" else 1]
    agent = session["agent"]
    payload = PairwisePayload(pair_id=pair_id, session_id=session_id, candidate_a_run_id=p["run_a"]["run_id"],
                              candidate_b_run_id=p["run_b"]["run_id"], version_a=f"{agent}:{session['version_a']}",
                              version_b=f"{agent}:{session['version_b']}", shown_order=p["shown_order"],
                              choice=choice, reason=reason.strip(), held_out=p["held_out"])
    record = PreferenceRecord(
        feedback_id=preference_store.new_feedback_id(), signal_type=SignalType.PAIRWISE,
        incident_id=p["run_b"]["incident_id"], run_id=p["run_b"]["run_id"], langfuse_trace_id=p["run_b"]["trace_id"],
        scenario_id=p["scenario_id"], prompt_version_set=p["run_b"]["version_set"], agent=agent,
        reviewer_role=reviewer_role, reviewer_id=reviewer_id,
        payload={**payload.model_dump(), "adaptation_id": session.get("adaptation_id")},
        created_at=at or datetime.now(timezone.utc), integrity=PreferenceIntegrity())
    written = preference_store.append(record)
    outcome, final = preference_store.pair_outcome(pair_id)
    log_interaction("pairwise_label_recorded", component="pairwise_session", stage="label", session_id=session_id,
                    pair_id=pair_id, feedback_id=written.feedback_id, choice=choice, agreement=outcome,
                    final_choice=final, reviewer_role=reviewer_role.value, held_out=p["held_out"])
    langfuse_tracker.attach_pairwise([p["run_a"]["trace_id"], p["run_b"]["trace_id"]], choice, pair_id)
    return written


def result(session_id: str, held_out_only: bool = True) -> dict[str, Any]:
    """Agreement counts and the win rate of version B over A (ties count 0.5) over decided pairs."""
    session = load(session_id)
    counts = {"agreed": 0, "tie_broken": 0, "disputed": 0, "single": 0, "none": 0}
    scores: list[float] = []
    for p in session["pairs"]:
        status, final = preference_store.pair_outcome(p["pair_id"])
        counts[status] += 1
        if held_out_only and not p["held_out"]:
            continue
        if status in ("agreed", "tie_broken") and final:
            scores.append({"B": 1.0, "A": 0.0, "tie": 0.5}[final])
    labelled = counts["agreed"] + counts["tie_broken"] + counts["disputed"]
    return {"session_id": session_id, "pairs": len(session["pairs"]), **counts,
            "held_out_pairs": sum(p["held_out"] for p in session["pairs"]), "decided_pairs_counted": len(scores),
            "win_rate_b": round(sum(scores) / len(scores), 4) if scores else None,
            "agreement_rate": round(counts["agreed"] / labelled, 4) if labelled else None}


def summarize(session_id: str) -> dict[str, Any]:
    """Log the session result to eval.log (evidence step E-9)."""
    session, outcome = load(session_id), result(session_id)
    agent = session["agent"]
    log_eval("pairwise_label_recorded", component="pairwise_session", stage="summary", session_id=session_id,
             adaptation_id=session.get("adaptation_id"),
             versions_compared=[f"{agent}:{session['version_a']}", f"{agent}:{session['version_b']}"],
             held_out_pairs=outcome["held_out_pairs"], agreed_pairs=outcome["agreed"] + outcome["tie_broken"],
             win_rate_new=outcome["win_rate_b"], agreement_rate=outcome["agreement_rate"])
    return outcome


def length_change_pct(session_id: str) -> Optional[float]:
    """Average change in the compared part's length, B relative to A (the length-bias check)."""
    session = load(session_id)
    a_len, b_len = [], []
    for p in session["pairs"]:
        for key, sink in (("run_a", a_len), ("run_b", b_len)):
            part = agent_part(store.load_output(p[key]["run_id"]), session["agent"])
            sink.append(len(json.dumps(part, ensure_ascii=False)))
    if not a_len or not sum(a_len):
        return None
    return round((sum(b_len) - sum(a_len)) / sum(a_len) * 100, 1)

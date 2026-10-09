"""Reward score per prompt version set (Architecture Spec Section 10.6; Req. FR-61, Section 12.6).

    mean_rating       mean of the four 1-5 ratings, scaled (r - 1) / 4      weight 0.35
    win_rate          decided pairwise comparisons the version took part in  weight 0.30
    approval_rate     approve / all reviews                                  weight 0.15
    fix_outcome_rate  (worked + 0.5 x partly) / outcomes except not_tried    weight 0.20

Missing components are dropped and the other weights renormalized. Fewer than
`reward_min_signals` signals: the score is shown as "not enough feedback".
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Optional

from src import config
from src.logger_setup import log_eval
from src.schemas.enums import SignalType
from src.schemas.feedback import RewardComponents, RewardScore

from . import pairwise_session, preference_store


def version_key(version_set: dict[str, str]) -> str:
    return "|".join(f"{agent}:{version}" for agent, version in sorted(version_set.items()))


def _win_rate(version_set: dict[str, str]) -> tuple[Optional[float], int]:
    scores: list[float] = []
    for session in pairwise_session.sessions():
        for p in session["pairs"]:
            status, final = preference_store.pair_outcome(p["pair_id"])
            if status not in ("agreed", "tie_broken") or not final:
                continue
            for side in ("A", "B"):
                if p[f"run_{side.lower()}"]["version_set"] == version_set:
                    scores.append(1.0 if final == side else 0.5 if final == "tie" else 0.0)
    return (mean(scores) if scores else None), len(scores)


def compute(version_set: dict[str, str], *, log: bool = True) -> RewardScore:
    settings = config.get_settings()
    ratings = [r.payload for r in preference_store.query(SignalType.RATING, prompt_version_set=version_set)]
    reviews = [r.payload for r in preference_store.query(SignalType.REVIEW, prompt_version_set=version_set)]
    outcomes = [r.payload.get("fix_outcome") for r in
                preference_store.query(SignalType.FIX_OUTCOME, prompt_version_set=version_set)]
    tried = [o for o in outcomes if o != "not_tried"]
    win_rate, pairs = _win_rate(version_set)
    components = RewardComponents(
        mean_rating=mean((mean(r[k] for k in ("rca", "actions", "severity", "summary")) - 1) / 4
                         for r in ratings) if ratings else None,
        win_rate=win_rate,
        approval_rate=sum(r.get("decision") == "approve" for r in reviews) / len(reviews) if reviews else None,
        fix_outcome_rate=(sum(1.0 if o == "worked" else 0.5 if o == "partly" else 0.0 for o in tried) / len(tried)
                          if tried else None))
    weights = settings.reward_weights
    present = {k: v for k, v in components.model_dump().items() if v is not None}
    total_weight = sum(weights[k] for k in present)
    score = round(sum(weights[k] * v for k, v in present.items()) / total_weight, 4) if present else None
    count = len(ratings) + len(reviews) + len(tried) + pairs
    reward = RewardScore(prompt_version_set_key=version_key(version_set), components=components,
                         weights=weights, score=score, signal_count=count,
                         sufficient=count >= settings.reward_min_signals, computed_at=datetime.now(timezone.utc))
    if log:
        log_eval("reward_computed", component="reward_model", prompt_version_set=version_set,
                 reward_score=score, signal_count=count, sufficient=reward.sufficient,
                 components=components.model_dump())
        _push(version_set, reward)
    return reward


def _push(version_set: dict[str, str], reward: RewardScore) -> None:
    from src.evaluation import langfuse_tracker

    traced = [r.langfuse_trace_id for r in preference_store.query(prompt_version_set=version_set)
              if r.langfuse_trace_id]
    if traced:
        langfuse_tracker.attach_reward(traced[-1], reward.prompt_version_set_key, reward.score, reward.signal_count)


def version_sets() -> list[dict[str, str]]:
    """Every prompt version set that has signals, plus the active one."""
    from src.agent import prompts

    found: dict[str, dict[str, str]] = {version_key(prompts.active_version_set()): prompts.active_version_set()}
    for r in preference_store.query(include_duplicates=False):
        if r.prompt_version_set:
            found.setdefault(version_key(r.prompt_version_set), dict(r.prompt_version_set))
    for session in pairwise_session.sessions():
        for p in session["pairs"]:
            for side in ("run_a", "run_b"):
                found.setdefault(version_key(p[side]["version_set"]), dict(p[side]["version_set"]))
    return list(found.values())


def compute_all(log: bool = True) -> list[RewardScore]:
    return [compute(v, log=log) for v in version_sets()]

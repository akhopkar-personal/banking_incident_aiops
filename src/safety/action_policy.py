"""Action policy (Architecture Spec Section 9.3; Req. FR-65).

`check` turns the Recommendation Agent's proposed actions into recommended
actions: denied and unknown action types are removed, risk is raised to the
policy level, a high-risk first step is moved later, and actions that need a
runbook citation but have none are dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from src import config
from src.schemas.analysis import ProposedAction
from src.schemas.enums import RiskLevel
from src.schemas.output import RecommendedAction

_RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


@dataclass(frozen=True)
class ActionRule:
    risk_level: RiskLevel
    requires_runbook: bool
    can_be_first_step: bool
    two_step_confirmation: bool


@dataclass(frozen=True)
class ActionPolicy:
    version: str
    action_types: dict[str, ActionRule]
    deny: frozenset[str]


def load_policy(path: Optional[Path] = None) -> ActionPolicy:
    path = Path(path) if path else config.get_settings().reference_file("action_policy.json")
    return _load(str(path), path.stat().st_mtime)


@lru_cache(maxsize=4)
def _load(path: str, _mtime: float) -> ActionPolicy:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rules = {name: ActionRule(RiskLevel(r["risk_level"]), bool(r["requires_runbook"]), bool(r["can_be_first_step"]),
                              bool(r["two_step_confirmation"]))
             for name, r in data["action_types"].items()}
    return ActionPolicy(version=data["version"], action_types=rules, deny=frozenset(data["deny"]))


def check(actions: list[ProposedAction], policy: Optional[ActionPolicy] = None
          ) -> tuple[list[RecommendedAction], list[str]]:
    """Apply the policy. Returns the recommended actions (renumbered from 1) and the run-level flags."""
    policy = policy or load_policy()
    flags: list[str] = []

    def flag(name: str) -> None:
        if name not in flags:
            flags.append(name)

    kept: list[RecommendedAction] = []
    for proposed in sorted(actions, key=lambda a: a.step):
        if proposed.action_type in policy.deny:
            flag("action_denied")
            continue
        rule = policy.action_types.get(proposed.action_type)
        if rule is None:
            flag("unknown_action_type")
            continue
        action_flags: list[str] = []
        risk = proposed.risk_level
        if _RISK_ORDER[risk] < _RISK_ORDER[rule.risk_level]:
            risk = rule.risk_level
            action_flags.append("risk_raised")
            flag("risk_raised")
        if rule.requires_runbook and not proposed.runbook_citation.strip():
            flag("uncited_high_risk" if rule.risk_level == RiskLevel.HIGH else "uncited_action_removed")
            continue
        if rule.two_step_confirmation:
            action_flags.append("two_step_confirmation")
        kept.append(RecommendedAction(
            step=1, action=proposed.action, action_type=proposed.action_type, risk_level=risk,
            runbook_citation=proposed.runbook_citation, expected_effect=proposed.expected_effect,
            requires_approval=True, policy_flags=action_flags,
        ))

    # A step that may not come first (every high-risk type) moves after the first step that may.
    if kept and not policy.action_types[kept[0].action_type].can_be_first_step:
        movable = next((i for i, a in enumerate(kept) if policy.action_types[a.action_type].can_be_first_step), None)
        if movable is None:
            # Only steps that may not come first remain: investigate before acting.
            kept.insert(0, RecommendedAction(
                step=1, action="Investigate further and confirm the diagnosis before any high-risk step",
                action_type="investigate_further", risk_level=RiskLevel.LOW, runbook_citation="",
                expected_effect="Confirms the cause before a high-risk change", policy_flags=["inserted_by_policy"],
            ))
        else:
            kept.insert(0, kept.pop(movable))
        for action in kept[1:]:
            if not policy.action_types.get(action.action_type, ActionRule(RiskLevel.LOW, False, True, False)
                                           ).can_be_first_step and "reordered" not in action.policy_flags:
                action.policy_flags.append("reordered")
        flag("reordered")

    for number, action in enumerate(kept, start=1):
        action.step = number
    return kept, flags

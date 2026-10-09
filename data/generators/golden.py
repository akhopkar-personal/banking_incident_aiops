"""Seed of the golden dataset: data/test_inputs.json and data/eval_rubric.json
(Req. Section 12.1; Architecture Spec Section 16.4).

Five variants per scenario give 25 cases. The files are written once; after that
they belong to the SME and the feedback loop (feedback_loop.promote appends to
them), so scripts/generate_data.py only rewrites them with --write-golden.
"""

from __future__ import annotations

import json
from typing import Any

from .builder import ScenarioBuilder, iso
from .scenarios import SCENARIOS, ScenarioDef

GOLDEN_VERSION = "golden-v1"
VARIANTS = ["replay", "free_text", "narrow_window", "wide_window", "missing_source"]


def _window(b: ScenarioBuilder, start: int, end: int) -> dict[str, str]:
    return {"start": iso(b.at(start)), "end": iso(b.at(end))}


def _inputs_for(s: ScenarioDef, b: ScenarioBuilder) -> list[dict[str, Any]]:
    code = s.scenario_id.replace("-", "")
    replay_raw = json.dumps({**s.alert, "fired_at": iso(b.at(s.reference_minute))}) if s.alert else ""
    common = {"scenario_id": s.scenario_id, "reference_time": iso(b.at(s.reference_minute)),
              "withheld_sources": []}
    replay = {**common, "input_mode": s.replay_mode, "raw_input": replay_raw}
    return [
        {"input_id": f"TI-{code}-REPLAY", "variant": "replay", **replay, "window": _window(b, 0, 90)},
        # No window: intake applies the default (-60/+15 minutes around reference_time, Req. Section 6.3).
        {"input_id": f"TI-{code}-FREETEXT", "variant": "free_text", **common, "input_mode": "free_text",
         "raw_input": s.free_text, "window": None},
        {"input_id": f"TI-{code}-NARROW", "variant": "narrow_window", **replay,
         "window": _window(b, s.onset_minute - 10, s.onset_minute + 20)},
        # Reaches 2 hours before the telemetry starts, so older, unrelated change records are in range.
        {"input_id": f"TI-{code}-WIDE", "variant": "wide_window", **replay, "window": _window(b, -120, 90)},
        {"input_id": f"TI-{code}-MISSING", "variant": "missing_source", **replay, "window": _window(b, 0, 90),
         "withheld_sources": [s.withheld_source]},
    ]


def _case_for(s: ScenarioDef, b: ScenarioBuilder, test_input: dict[str, Any]) -> dict[str, Any]:
    withheld = set(test_input["withheld_sources"])
    must_cite = [b.evidence[t] for t in s.must_cite_tags if b.evidence[t].split("-")[0] not in withheld]
    causal = b.evidence[s.causal_change_tag] if s.causal_change_tag else None
    if causal and "DEP" in withheld:
        causal = None
    major = s.expected_severity in ("S1", "S2")
    return {
        "case_id": test_input["input_id"].replace("TI-", "GC-"),
        "input_id": test_input["input_id"],
        "scenario_id": s.scenario_id,
        "variant": test_input["variant"],
        "expected_root_cause": s.root_cause,
        "expected_severity": s.expected_severity,
        "expected_issue_class": s.issue_class,
        "expected_runbook_id": s.runbook_id,
        "expected_doc_ids": s.expected_doc_ids,
        "must_cite_evidence_ids": must_cite,
        "expected_causal_change_id": causal,
        "expected_dispatch": {
            "route": "page" if major else "assign",
            "fast_path": s.expected_severity == "S1",
            "queue": "incident-escalation" if major else "production-support",
        },
        "expected_regulatory_flag": s.regulatory,
        # With the deciding source withheld, the correct behaviour is to abstain
        # (INSUFFICIENT_EVIDENCE or needs_human_rca), not to guess (Req. Section 12.2).
        "expected_abstention": bool(withheld),
        "version_added": GOLDEN_VERSION,
    }


def golden_files(builders: dict[str, ScenarioBuilder]) -> tuple[list[dict], dict]:
    """(test_inputs, eval_rubric) built from the scenario builders."""
    inputs, cases = [], []
    for scenario_id, s in SCENARIOS.items():
        b = builders[scenario_id]
        for test_input in _inputs_for(s, b):
            inputs.append(test_input)
            cases.append(_case_for(s, b, test_input))
    rubric = {
        "version": GOLDEN_VERSION,
        "status": "draft: ground truth pending SME validation (Req. Section 9.4)",
        "cases": cases,
    }
    return inputs, rubric

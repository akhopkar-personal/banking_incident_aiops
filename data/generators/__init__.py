"""Synthetic data generator (Architecture Spec Section 16). Entry point:
`python scripts/generate_data.py`. Output is deterministic: the same code
always produces byte-identical files."""

from __future__ import annotations

from pathlib import Path

from .baseline import add_healthy_baseline
from .builder import ScenarioBuilder, iso
from .fixtures import FIXTURES, FixtureDef
from .scenarios import SCENARIOS, ScenarioDef

GENERATOR_VERSION = "gen-v1"

__all__ = ["GENERATOR_VERSION", "SCENARIOS", "FIXTURES", "ScenarioDef", "FixtureDef", "build", "generate_all"]


def build(dataset_id: str) -> ScenarioBuilder:
    """Build (but do not write) one scenario or fixture."""
    fixture = FIXTURES.get(dataset_id)
    scenario = SCENARIOS[fixture.base_scenario if fixture else dataset_id]

    b = ScenarioBuilder(dataset_id, scenario.start, scenario.seed)
    add_healthy_baseline(b)
    scenario.build(b)
    if fixture and fixture.overlay:
        fixture.overlay(b)
    b.finalize()
    if fixture and fixture.post:
        fixture.post(b)

    b.manifest = {
        "dataset_id": dataset_id,
        "kind": "fixture" if fixture else "scenario",
        "title": fixture.purpose if fixture else scenario.title,
        "base_scenario": scenario.scenario_id if fixture else None,
        "generator_version": GENERATOR_VERSION,
        "seed": scenario.seed,
        "window_start": iso(b.at(0)),
        "window_end": iso(b.at(b.minutes)),
        "onset": iso(b.at(scenario.onset_minute)),
        "reference_time": iso(b.at(scenario.reference_minute)),
        "key_evidence": dict(sorted(b.evidence.items())),
        **b.manifest,
    }
    if not fixture:
        b.manifest.update(
            must_cite_evidence_ids=[b.evidence[t] for t in scenario.must_cite_tags],
            causal_change_id=b.evidence[scenario.causal_change_tag] if scenario.causal_change_tag else None,
            red_herring_ids=[b.evidence[t] for t in scenario.red_herring_tags],
            expected_rules_pass1=scenario.expected_rules_pass1,
            ground_truth_status="draft, pending SME validation",
        )
    return b


def generate_all(telemetry_dir: Path) -> dict[str, ScenarioBuilder]:
    """Write every scenario and fixture under `telemetry_dir`; return the builders by ID."""
    from src.config import SCENARIO_DIRS

    builders = {}
    for dataset_id in [*SCENARIOS, *FIXTURES]:
        b = build(dataset_id)
        b.write(telemetry_dir / SCENARIO_DIRS[dataset_id])
        builders[dataset_id] = b
    return builders

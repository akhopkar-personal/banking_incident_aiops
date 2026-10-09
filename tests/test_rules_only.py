"""Rules-only workflow end to end (Req. FR-38, FR-42, FR-43, FR-51, FR-68, FR-69). T-FALLBACK, T-DEDUP, T-ROUTE."""

from __future__ import annotations

import pytest

from data.generators import SCENARIOS
from src.schemas.enums import IncidentState, RunMode, SeverityLevel
from src.services import incident_state_store as store
from src.services.rules_only_runner import run_rules_only
from src.tools.dispatch_mocks import outbox


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_each_scenario_is_dispatched_by_rules(sandbox, scenario_id):
    s = SCENARIOS[scenario_id]
    result = run_rules_only(scenario_id)
    assert result.created and result.rules_pass1.level.value == s.expected_severity
    d = result.dispatch
    assert d.itsm_ticket_id == "MOCK-ITSM-0001" and d.paged  # every scenario is Major
    assert d.fast_path == (s.expected_severity == "S1")
    pages = outbox.read("pages")
    assert len(pages) == 1 and pages[0]["team"] == "incident-escalation"
    view = store.current(result.incident_id)
    assert view.state == IncidentState.PAGED and view.events[:1] == ["created"]
    assert result.incident_id == f"INC-{s.start:%Y%m%d}-001"


def test_fast_path_page_is_written_before_the_ticket(sandbox):
    result = run_rules_only("SC-03")
    events = store.current(result.incident_id).events
    assert events.index("paged") < events.index("ticket_upserted")


def test_replay_twice_is_deduplicated(sandbox):
    first = run_rules_only("SC-01")
    second = run_rules_only("SC-01")
    assert second.deduplicated and second.incident_id == first.incident_id and not second.dispatch.paged
    assert len(outbox.read("pages")) == 1
    assert len([l for l in outbox.read("itsm") if l.get("created")]) == 1


def test_alert_storm_gives_one_incident_ticket_and_page(sandbox):
    result = run_rules_only("FX-ALERT-STORM")
    assert result.groups == 1 and result.anomaly_count >= 7
    assert len(store.list_incidents()) == 1 and len(outbox.read("pages")) == 1
    assert len({l["ticket_id"] for l in outbox.read("itsm")}) == 1


def test_llm_down_fixture_still_pages_s1(sandbox):
    sandbox.llm_enabled = False
    result = run_rules_only("FX-LLM-DOWN")
    assert result.rules_pass1.level == SeverityLevel.S1 and result.dispatch.fast_path
    assert '"event": "kill_switch_active"' in (sandbox.logs_dir / "interactions.log").read_text(encoding="utf-8")


def test_failure_fixture_still_pages(sandbox):
    result = run_rules_only("FX-FAILURE")
    assert result.created and result.dispatch.paged and result.rules_pass1.level == SeverityLevel.S1


def test_evaluation_mode_writes_no_outbox(sandbox):
    result = run_rules_only("SC-02", run_mode=RunMode.EVALUATION)
    assert result.dispatch.suppressed and {"itsm_upsert", "page"} <= set(result.dispatch.intended)
    assert outbox.read("pages") == [] and outbox.read("itsm") == []


def test_missing_source_variant_keeps_severity(sandbox):
    result = run_rules_only("SC-05", withheld_sources=["KFK"])
    assert result.rules_pass1.level == SeverityLevel.S2 and "R-S2-DUP" not in {
        r.rule_id for r in result.rules_pass1.rules_fired}
    assert result.incident.withheld_sources == ["KFK"]

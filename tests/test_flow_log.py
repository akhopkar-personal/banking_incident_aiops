"""The flow log of one run, assembled from the three JSON log files."""

from __future__ import annotations

import json

from src.agent.core_agent import run_investigation
from src.services import flow_log


def test_flow_log_follows_one_run_through_the_stages(fake):
    fake("SC-01")
    first = run_investigation(scenario_id="SC-01")
    fake("SC-02")
    second = run_investigation(scenario_id="SC-02")
    entries = flow_log.load(first.run_id)
    assert entries and {e["run_id"] for e in entries} == {first.run_id}  # the other run is left out
    assert [e["timestamp"] for e in entries] == sorted(e["timestamp"] for e in entries)
    assert entries[0]["flow_elapsed_s"] == 0.0 and entries[0]["flow_stage"] == "Detect"
    order = [flow_log.STAGES.index(e["flow_stage"]) for e in entries if e["flow_stage"] in flow_log.STAGES]
    assert set(order) >= {0, 1, 2, 3}
    events = {e["event"] for e in entries}
    assert {"severity_rules_evaluated", "fast_path_page", "llm_call", "tool_call", "node_completed"} <= events
    totals = flow_log.summary(entries)
    assert totals["lines"] == len(entries) and totals["llm_calls"] >= 4 and totals["tool_calls"] > 0
    assert totals["errors"] == 0
    rules = next(e for e in entries if e["event"] == "severity_rules_evaluated")
    assert flow_log.describe(rules).startswith("pass1: S1 (")
    text = flow_log.to_text(entries)
    assert text.startswith(f"Flow log of {first.run_id}, incident {first.incident_id}")
    assert "[Triage] dispatch_fast_page | fast_path_page: fast-path page (new)" in text
    assert flow_log.load(second.run_id) and flow_log.load("RUN-NONE") == []


def test_rotated_files_errors_and_unknown_events(tmp_path):
    def line(ts, event, **fields):
        return json.dumps({"timestamp": f"2026-10-11T10:00:{ts}+00:00", "level": fields.pop("level", "INFO"),
                           "event": event, "run_id": fields.pop("run_id", "RUN-1"), **fields})

    (tmp_path / "interactions.log.1").write_text(line("00.000", "anomaly_detected", component="anomaly_detector",
                                                      service="auth-service", signal="api_p95_ms") + "\n",
                                                 encoding="utf-8")
    (tmp_path / "interactions.log").write_text("not json\n" + line("05.500", "made_up", component="mystery",
                                                                   colour="blue") + "\n"
                                               + line("06.000", "node_completed", component="finalize",
                                                      run_id="RUN-2") + "\n", encoding="utf-8")
    (tmp_path / "error.log").write_text(line("03.250", "system_error", component="triage_agent", level="ERROR",
                                             stage="node_failed", error_type_name="TimeoutError",
                                             error_message="took too long", stack_trace="Traceback ...") + "\n",
                                        encoding="utf-8")
    entries = flow_log.load("RUN-1", tmp_path)
    assert [(e["event"], e["log_file"], e["flow_stage"], e["flow_elapsed_s"]) for e in entries] == [
        ("anomaly_detected", "interactions", "Detect", 0.0),
        ("system_error", "error", "Triage", 3.25),
        ("made_up", "interactions", "Other", 5.5),
    ]
    assert flow_log.describe(entries[1]) == "failed in triage_agent: TimeoutError (took too long)"
    assert flow_log.describe(entries[2]) == "colour blue"
    assert flow_log.summary(entries)["errors"] == 1
    raw = [json.loads(x) for x in flow_log.to_jsonl(entries).splitlines()]
    assert raw[1]["stack_trace"] == "Traceback ..." and raw[1]["stage"] == "node_failed"
    assert not set(flow_log.ADDED_FIELDS) & set(raw[1])

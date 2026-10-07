"""Logging (Architecture Spec Section 13; Req. Section 16)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src import logger_setup
from src.logger_setup import (
    ALLOWED_EVENTS,
    configure_logging,
    log_context,
    log_error,
    log_eval,
    log_interaction,
)
from src.safety.redaction import redact, register_secret_provider

REQ_SECTION_16 = Path(__file__).resolve().parents[1] / "docs"


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def logs(isolated_settings, tmp_path):
    directory = configure_logging(tmp_path / "logs")
    return directory


def test_interaction_line_has_common_fields(logs):
    with log_context(incident_id="INC-20260314-001", run_id="RUN-1", trace_id="t-1",
                     langfuse_trace_id="lf-1", prompt_version_set={"rca_agent": "v2"}):
        log_interaction("tool_call", component="rca_agent", tool="query_logs", record_count=12)
    (line,) = read_lines(logs / "interactions.log")
    assert line["event"] == "tool_call" and line["component"] == "rca_agent" and line["level"] == "INFO"
    assert line["incident_id"] == "INC-20260314-001" and line["run_id"] == "RUN-1"
    assert line["trace_id"] == "t-1" and line["langfuse_trace_id"] == "lf-1"
    assert line["prompt_version_set"] == {"rca_agent": "v2"}
    assert line["tool"] == "query_logs" and line["record_count"] == 12
    assert re.match(r"^\d{4}-\d{2}-\d{2}T.*\+00:00$", line["timestamp"])
    assert "message" not in line


def test_explicit_fields_override_context(logs):
    with log_context(incident_id="INC-20260314-001", run_id="RUN-1"):
        log_interaction("node_completed", component="triage_agent", run_id="RUN-2")
    assert read_lines(logs / "interactions.log")[0]["run_id"] == "RUN-2"


def test_field_named_like_a_logrecord_attribute_is_kept(logs):
    log_eval("eval_result", component="deepeval_harness", message="ok", name="golden", args=[1])
    line = read_lines(logs / "eval.log")[0]
    assert line["message"] == "ok" and line["name"] == "golden" and line["args"] == [1]


def test_unknown_event_rejected(logs):
    with pytest.raises(ValueError, match="unknown log event"):
        log_interaction("made_up_event", component="x", incident_id="INC-20260314-001", run_id="RUN-1")


def test_run_scoped_event_needs_ids(logs):
    with pytest.raises(ValueError, match="run-scoped"):
        log_interaction("dispatch_page", component="gates")
    log_eval("reward_computed", component="reward_model", reward_score=0.71)  # not run-scoped


def test_component_required(logs):
    with pytest.raises(ValueError, match="component"):
        log_eval("eval_result", component="")


def test_files_are_separate(logs):
    with log_context(incident_id="INC-20260314-001", run_id="RUN-1"):
        log_interaction("node_completed", component="intake")
        log_eval("eval_result", component="harness", rca_top1=0.6)
        log_error("system_error", component="system_error", failed_node="triage_agent")
    assert [l["event"] for l in read_lines(logs / "interactions.log")] == ["node_completed"]
    assert [l["event"] for l in read_lines(logs / "eval.log")] == ["eval_result"]
    (err,) = read_lines(logs / "error.log")
    assert err["event"] == "system_error" and err["level"] == "ERROR"


def test_error_with_exception_has_redacted_stack_trace(logs):
    try:
        raise RuntimeError("connection to postgres://admin:hunter2@db:5432 failed")
    except RuntimeError as exc:
        with log_context(incident_id="INC-20260314-001", run_id="RUN-1"):
            log_error("system_error", component="rca_agent", exc=exc)
    (line,) = read_lines(logs / "error.log")
    assert line["error_type_name"] == "RuntimeError"
    assert "Traceback" in line["stack_trace"]
    assert "hunter2" not in json.dumps(line)


def test_secrets_and_emails_redacted_everywhere(logs, monkeypatch):
    with log_context(incident_id="INC-20260314-001", run_id="RUN-1"):
        log_interaction("llm_call", component="triage_agent",
                        prompt="key voc-AAAAAAAAAAAAAAAAAAAA contact jane.doe@bank.example",
                        nested={"headers": ["Authorization: Bearer abcdefghijklmnopqrstuvwxyz"],
                                "cfg": "password=s3cret"})
    text = (logs / "interactions.log").read_text(encoding="utf-8")
    for leaked in ("voc-AAAAAAAAAAAAAAAAAAAA", "jane.doe@bank.example", "abcdefghijklmnopqrstuvwxyz", "s3cret"):
        assert leaked not in text, leaked


def test_configured_secret_values_redacted(logs):
    # Any configured secret is scrubbed, even one that matches no pattern.
    register_secret_provider(lambda: ["custom-secret-value-123"])
    log_eval("eval_result", component="harness", note="leak custom-secret-value-123 here")
    assert "custom-secret-value-123" not in (logs / "eval.log").read_text(encoding="utf-8")


def test_context_resets_after_block(logs):
    with log_context(incident_id="INC-20260314-001", run_id="RUN-1"):
        pass
    assert logger_setup.current_context() == {}


def test_allowed_events_cover_requirements_section_16():
    """Every event name listed in Req. Section 16 is accepted by the logger."""
    req = next(REQ_SECTION_16.glob("AI_Capstone_Project_Requirements_v*.md"))
    line = next(l for l in req.read_text(encoding="utf-8").splitlines() if l.startswith("| `event` |"))
    listed = set(re.findall(r"`([a-z_]+)`", line)) - {"event"}
    assert listed, "could not find the event list in Req. Section 16"
    assert listed <= ALLOWED_EVENTS, sorted(listed - ALLOWED_EVENTS)


@pytest.mark.parametrize("text,flag", [
    ("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----", "secret_pem"),
    ("sk-proj-ABCDEFGHIJKLMNOPQRST", "secret_api_key"),
    ("pk-lf-00000000-0000-0000-0000-000000000000", "secret_api_key"),
    ("api_key: 'abc123'", "secret_assignment"),
    ("https://user:pw@host/path", "secret_url_credentials"),
    ("mail ops@bank.example now", "pii_email"),
])
def test_redaction_patterns(text, flag):
    redacted, flags = redact(text)
    assert flag in flags and redacted != text


def test_redaction_leaves_normal_text():
    text = "payments-service error rate 38% after release v2.14.0 (DEP-0007)"
    assert redact(text) == (text, [])

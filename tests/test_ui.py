"""Streamlit pages, run headless with AppTest against the sandbox and the scripted model
(Req. Section 15; Architecture Spec Section 14)."""

from __future__ import annotations

import time

import pytest
from streamlit.testing.v1 import AppTest

from src.agent.core_agent import run_investigation
from src.config import REPO_ROOT
from src.services import incident_state_store as store
from src.tools.dispatch_mocks import outbox

UI = REPO_ROOT / "src" / "ui"
INCIDENT, OUTBOX, RESOLVE = "pages/1_Incident.py", "pages/2_Outbox.py", "pages/3_Resolve_and_Verify.py"


@pytest.fixture
def ui(sandbox, monkeypatch):
    """open(page, **session_state) runs a page headless. Environment variables the app exports
    are restored after the test."""
    for name in ("OPENAI_BASE_URL", "LANGFUSE_HOST", "LANGFUSE_BASE_URL", "DEEPEVAL_TELEMETRY_OPT_OUT"):
        monkeypatch.setenv(name, "set-by-test")

    def open_page(name: str = "app.py", **session) -> AppTest:
        at = AppTest.from_file(str(UI / "app.py"), default_timeout=90)  # registers the pages
        for key, value in session.items():
            at.session_state[key] = value
        if name != "app.py":
            at.switch_page(name)
        at.run()
        assert not at.exception, at.exception
        return at

    return open_page


@pytest.fixture
def investigated(fake):
    def go(scenario_id: str = "SC-02", **oracle):
        fake(scenario_id if scenario_id.startswith("SC") else "SC-01", **oracle)
        return run_investigation(scenario_id=scenario_id)
    return go


def texts(at: AppTest) -> str:
    parts = [e.value for kind in ("markdown", "caption", "info", "success", "warning", "error", "subheader", "title")
             for e in getattr(at, kind)]
    return "\n".join(str(p) for p in parts)


def button(at: AppTest, label: str):
    return next(b for b in at.button if b.label == label)


def widget(at: AppTest, kind: str, label: str):
    return next(w for w in getattr(at, kind) if w.label == label)


# ------------------------------------------------------------- Investigate


def test_investigate_replay_shows_node_progress_and_the_result(ui, fake):
    fake("SC-01")
    at = ui()
    at.selectbox(key="scenario_id").set_value("SC-01")
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    page = texts(at)
    for label in ("Intake: read and classify the input", "Severity rules, pass 1", "Critical fast path: page",
                  "Triage Agent", "RCA Agent", "Change Correlation Agent", "Recommendation Agent", "Finalize"):
        assert f"**{label}**" in page
    assert "**Recommendation Agent**: 2 actions proposed" in page
    assert "**Output guardrails**: actions kept: 1; flags: " in page and "action_denied" in page
    assert "**Page the escalation team**: added the recommendation to the earlier page" in page
    assert "Rules severity: **S1" in page and "Issue class: **release_regression**" in page
    assert "Fast-path page: **simulated page sent" in page
    assert at.status[0].label.startswith("Finished") and "Result: INC-20260314-001" in page
    assert "Pending review (PENDING_REVIEW)" in page and "SIMULATED" in page
    assert at.session_state["selected_incident"] == "INC-20260314-001"
    assert not at.button(key="run").disabled


def test_investigate_rejects_text_that_is_not_an_incident(ui, fake):
    fake("SC-02")
    at = ui()
    at.selectbox(key="scenario_id").set_value("SC-02")
    at.radio(key="input_mode").set_value("Free text").run()
    at.text_area(key="raw_SC-02_free_text").set_value("Lunch is at noon today.")
    at.button(key="run").click().run()
    assert "does not describe an incident" in texts(at)
    assert store.list_incidents() == []


def test_investigate_alert_json_with_an_evaluation_run(ui, fake):
    fake("SC-01")
    at = ui()
    at.selectbox(key="scenario_id").set_value("SC-02")
    at.radio(key="input_mode").set_value("Alert JSON").run()
    assert '"timestamp": "2026-03-21' in at.text_area(key="raw_SC-02_alert_json").value  # template
    at.selectbox(key="scenario_id").set_value("SC-01").run()
    assert "PaymentsErrorRateHigh" in at.text_area(key="raw_SC-01_alert_json").value  # golden example
    at.toggle(key="evaluation_mode").set_value(True)
    at.button(key="run").click().run()
    assert "Evaluation mode: the dispatch decisions were recorded but nothing was sent." in texts(at)
    assert outbox.read("pages") == []


# ---------------------------------------------------------------- Incident


def test_incident_page_shows_the_analysis_and_records_an_approval(ui, investigated):
    out = investigated("SC-02")
    at = ui(INCIDENT, role="escalation")
    assert [t.label for t in at.tabs] == ["Summary", "Hypotheses and evidence", "Changes", "Actions", "Timeline",
                                         "Dispatch", "Review"]
    page = texts(at)
    assert "Pending review (PENDING_REVIEW)" in page
    assert any(out.hypotheses[0].root_cause in e.label for e in at.expander)
    assert "Advisory only" in page and "Policy flags" not in page  # the denied action was removed
    widget(at, "radio", "Decision").set_value("approve")
    button(at, "Submit review").click().run()
    assert not at.exception, at.exception
    assert "Review recorded: APPROVED." in texts(at)
    assert store.current(out.incident_id).reviews[out.run_id]["status"] == "APPROVED"
    assert "Approved (APPROVED)" in texts(at)


def test_incident_edit_needs_a_change_and_a_reason(ui, investigated):
    out = investigated("SC-02")
    at = ui(INCIDENT, role="production_support")
    widget(at, "radio", "Decision").set_value("edit")
    widget(at, "text_area", "Reason (required for Edit and Reject)").set_value("Too vague")
    widget(at, "selectbox", "Issue type (required for Edit and Reject)").set_value("verbosity")
    button(at, "Submit review").click().run()
    assert "Edit needs at least one changed field" in texts(at)
    widget(at, "text_area", "Stakeholder summary").set_value("Payments are delayed for some customers.")
    button(at, "Submit review").click().run()
    assert "Feedback candidate FC-0001 created." in texts(at)
    review = store.current(out.incident_id).reviews[out.run_id]
    assert review["status"] == "EDITED" and review["edited_output"] == {
        "stakeholder_summary": "Payments are delayed for some customers."}


def test_system_error_has_its_own_layout(ui, investigated):
    investigated("FX-LLM-DOWN")
    at = ui(INCIDENT, role="escalation")
    page = texts(at)
    assert "System error: no recommendation (SYSTEM_ERROR)" in page
    assert "The on-call team was paged based on monitoring rules." in page
    assert not at.tabs and "Submit review" not in [b.label for b in at.button]


def test_acknowledge_and_reclassify_from_the_incident_page(ui, investigated):
    out = investigated("SC-02")
    at = ui(INCIDENT, role="escalation")
    at.button(key=f"ack_{out.incident_id}").click().run()
    assert "Page acknowledged after" in texts(at)
    assert "Production Support can re-classify" in texts(at)
    at = ui(INCIDENT, role="production_support")
    widget(at, "text_area", "Reason (required)").set_value("Balances wrong for many customers")
    button(at, "Re-classify and page").click().run()
    assert not at.exception, at.exception
    assert "Re-classified as S1." in texts(at)
    assert [p["mode"] for p in outbox.read("pages")] == ["new", "update"]
    assert store.current(out.incident_id).reclassifications[0]["to_level"] == "S1"


def test_review_controls_are_shown_only_to_the_receiving_team(ui, investigated):
    investigated("SC-02")
    at = ui(INCIDENT, role="sme")
    assert "Submit review" not in [b.label for b in at.button]
    assert "The receiving team reviews" in texts(at)


def test_needs_human_rca_hand_off(ui, investigated):
    out = investigated("SC-02", confidence=0.4)
    at = ui(INCIDENT, role="escalation")
    assert "Needs human RCA" in texts(at)
    widget(at, "radio", "Was the real cause among the ranked hypotheses?").set_value("No")
    button(at, "Record the hand-off result").click().run()
    assert store.current(out.incident_id).gate_handoffs[out.run_id]["found_in_ranked_list"] is False


# ------------------------------------------------------------------ Outbox


def test_outbox_lists_simulated_dispatch_and_resets(ui, investigated):
    investigated("SC-01")
    at = ui(OUTBOX)
    assert [m.value for m in at.metric] == ["2", "1", "3"]  # pages (new + update), ticket, notifications
    assert "SIMULATED" in texts(at) and len(at.dataframe) == 3
    assert at.button(key="reset_button").disabled
    at.checkbox(key="reset_incidents").check()
    at.checkbox(key="reset_sure").check().run()
    at.button(key="reset_button").click().run()
    assert "Outbox archived" in texts(at) and "Incident records archived" in texts(at)
    assert outbox.read("pages") == [] and store.list_incidents() == []


# -------------------------------------------------------- Resolve and Verify


def test_resolve_then_verify_indexes_the_incident(ui, investigated):
    out = investigated("SC-02")
    at = ui(RESOLVE, role="escalation")
    widget(at, "text_area", "Actions taken").set_value("Replaced broker 2")
    button(at, "Record the resolution").click().run()
    assert "Resolution recorded" in texts(at)
    at = ui(RESOLVE, role="production_support")
    assert "The SME or the escalation lead verifies" in texts(at)
    at = ui(RESOLVE, role="sme")
    widget(at, "radio", "Verdict").set_value("confirmed")
    button(at, "Record the verification").click().run()
    assert not at.exception, at.exception
    assert f"Indexed into the knowledge base as VR-{out.incident_id}" in texts(at)
    assert store.current(out.incident_id).state.value == "CLOSED_VERIFIED"


def test_idle_session_clears_page_selections(ui, investigated):
    out = investigated("SC-02")
    at = ui(INCIDENT, role="escalation", selected_incident=out.incident_id, last_activity=time.time() - 3600)
    assert "idle for more than 30 minutes" in texts(at)
    assert at.session_state["role"] == "escalation"


# ------------------------------------------------------- Phase 6 pages (4 to 7)

QUEUE, RLHF, ADAPT, EVAL = ("pages/4_Review_Queue.py", "pages/5_Feedback_and_RLHF.py",
                            "pages/6_Adaptation_History.py", "pages/7_Evaluation.py")


def rejected(reviewer: str):
    from src.feedback import review_store
    from src.schemas.enums import IssueType, ReviewerRole
    from src.schemas.feedback import Ratings, ReviewDecision

    out = run_investigation(scenario_id="SC-02")
    review_store.record_review(ReviewDecision(
        incident_id=out.incident_id, run_id=out.run_id, decision="reject", reason="Wrong broker blamed",
        issue_type=IssueType.ROOT_CAUSE_ERROR, ratings=Ratings(rca=2, actions=3, severity=4, summary=3),
        reviewer_role=ReviewerRole.ESCALATION, at=store.now()), reviewer)
    return out


def wait_for_task(at: AppTest) -> AppTest:
    from src.ui import runner

    task = runner.latest_task(runner.LEARNING)
    assert task is not None and task.done.wait(120), "the learning task did not finish"
    assert task.error is None, task.error
    at.run()
    assert not at.exception, at.exception
    return at


def test_review_queue_promotes_for_the_sme_only(ui, fake):
    from src.evaluation import golden_dataset

    fake("SC-02")
    rejected("rev-a")
    at = ui(QUEUE, role="escalation")
    assert "Promote" not in [b.label for b in at.button] and "done by the SME" in texts(at)
    at = ui(QUEUE, role="sme")
    assert "Wrong broker blamed" in texts(at)
    button(at, "Promote").click().run()
    assert not at.exception, at.exception
    assert "promoted. Golden dataset is now golden-v2" in texts(at)
    assert golden_dataset.case("GC-SC02-FB001")["expected_issue_class"] == "eventhub_broker"


def test_rlhf_page_labels_blind_pairs_and_shows_rewards(ui, fake):
    from src.evaluation import adaptation_engine
    from src.feedback import pairwise_session, preference_store

    fake("SC-02")
    adaptation_engine._add_version("rca_agent", {"type": "prompt_rule", "text": "Broker first."}, "ADP-T")
    session = pairwise_session.create_session("rca_agent", "v1", "v2", ["GC-SC02-REPLAY"], concurrency=1)
    at = ui(RLHF, role="escalation", reviewer_id="r1")
    assert "versions hidden" in texts(at) and "Output 1" in texts(at)
    widget(at, "text_input", "Why? (required)").set_value("Clearer evidence")
    button(at, "Record my choice").click().run()
    assert not at.exception, at.exception
    pair_id = session["pairs"][0]["pair_id"]
    assert preference_store.agreement(pair_id) == "single" and "Label recorded" in texts(at)
    assert "Nothing left for r1" in texts(at)
    button(at, "Export DPO file").click().run()
    assert "0 pairs written" in texts(at)  # one label is not an agreed pair
    assert any("not enough feedback" in str(v) for df in at.dataframe for v in df.value.get("reward", []))


def test_rlhf_page_creates_a_session_in_the_background(ui, fake):
    from src.evaluation import adaptation_engine
    from src.feedback import pairwise_session

    fake("SC-02")
    adaptation_engine._add_version("rca_agent", {"type": "prompt_rule", "text": "Broker first."}, "ADP-T")
    at = ui(RLHF, role="evaluation_engineer")
    widget(at, "multiselect", "Golden cases (each runs twice: once per version)").set_value(["GC-SC02-REPLAY"])
    button(at, "Create session").click().run()
    wait_for_task(at)
    [session] = pairwise_session.sessions()
    assert session["status"] == "ready" and session["version_b"] == "v2"
    assert "finished" in texts(at)


def test_adaptation_page_approve_and_apply(ui, fake):
    from src.evaluation import adaptation_engine
    from src.feedback import feedback_loop
    from src.schemas.enums import ReviewerRole

    fake("SC-02")
    rejected("rev-a")
    rejected("rev-b")
    for c in feedback_loop.review_queue():
        feedback_loop.promote(c.candidate_id, feedback_loop.default_ground_truth(c), ReviewerRole.SME, "sme")
    at = ui(ADAPT, role="production_support")
    assert "Scan feedback for patterns" not in [b.label for b in at.button]
    at = ui(ADAPT, role="sme")
    button(at, "Scan feedback for patterns").click().run()
    assert not at.exception, at.exception
    entry = next(e for e in adaptation_engine.entries() if e.trigger_pattern["kind"] == "root_cause_error")
    assert entry.status == "proposed" and entry.adaptation_id in " ".join(e.label for e in at.expander)
    approve = [b for b in at.button if b.label == "Approve"]
    kinds = [e.trigger_pattern["kind"] for e in reversed([x for x in adaptation_engine.entries()
                                                          if x.status == "proposed"])]
    approve[kinds.index("root_cause_error")].click().run()
    assert adaptation_engine.get(entry.adaptation_id).status == "approved"
    at.toggle(key=f"judge_{entry.adaptation_id}").set_value(False)
    at.radio(key=f"scope_{entry.adaptation_id}").set_value("Only SC-02 cases")
    at.button(key=f"apply_{entry.adaptation_id}").click().run()
    wait_for_task(at)
    assert adaptation_engine.get(entry.adaptation_id).status == "reverted"  # same scripted answers: no gain
    at.button(key=f"evidence_{entry.adaptation_id}").click().run()
    assert "missing E-7" in texts(at)  # never applied, so no adaptation_applied line


def test_evaluation_page_runs_the_golden_set(ui, fake):
    fake("SC-02")
    at = ui(EVAL, role="evaluation_engineer")
    assert "No golden-set run yet." in texts(at)
    at.toggle(key="eval_judge").set_value(False)
    at.selectbox(key="eval_scope").set_value("SC-02").run()
    at.button(key="run_golden").click().run()
    wait_for_task(at)
    page = texts(at)
    assert "Latest golden-set run: EVAL-" in page and "judge off" in page
    targets = at.dataframe[0].value
    assert "severity_match" in list(targets["metric"]) and len(at.dataframe[1].value) == 5

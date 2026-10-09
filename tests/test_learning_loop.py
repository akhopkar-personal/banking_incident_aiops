"""Learning loop (Architecture Spec Sections 10.3 to 10.7, 11; Req. Sections 12, 16.1):
golden dataset, evaluation harness, SME promotion, pairwise sessions, reward, DPO export,
adaptation engine and the evidence report. T-FEEDBACK, T-RLHF, T-ADAPT, T-EVIDENCE."""

from __future__ import annotations

import json
import typing
from typing import Any

import pytest

from src.agent import prompts
from src.agent.core_agent import run_investigation
from src.evaluation import adaptation_engine, deepeval_harness, evidence_report, golden_dataset
from src.evaluation.judge import FunctionCallingJudge
from src.feedback import dpo_exporter, feedback_loop, pairwise_session, preference_store, review_store, reward_model
from src.schemas.enums import IssueType, ReviewerRole, RunMode, SignalType
from src.schemas.feedback import Ratings, ReviewDecision
from src.services import incident_state_store as store
from src.tools.dispatch_mocks import outbox

SME, EE, ESC = ReviewerRole.SME, ReviewerRole.EVALUATION_ENGINEER, ReviewerRole.ESCALATION
SC02 = ["GC-SC02-REPLAY", "GC-SC02-FREETEXT"]
LOW = Ratings(rca=1, actions=2, severity=4, summary=3)


@pytest.fixture
def sc02(fake):
    fake("SC-02")


def events(sandbox, name: str = "interactions") -> list[dict[str, Any]]:
    path = sandbox.logs_dir / f"{name}.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def reject_run(reviewer: str, issue: IssueType = IssueType.ROOT_CAUSE_ERROR, reason: str = "Wrong broker blamed"):
    out = run_investigation(scenario_id="SC-02")
    review_store.record_review(ReviewDecision(
        incident_id=out.incident_id, run_id=out.run_id, decision="reject", reason=reason, issue_type=issue,
        ratings=LOW, reviewer_role=ESC, at=store.now()), reviewer)
    return out


# ------------------------------------------------------------ golden dataset


def test_add_case_appends_and_bumps_the_version(sandbox):
    assert golden_dataset.version() == "golden-v1" and len(golden_dataset.cases()) == 25
    case = {**golden_dataset.case("GC-SC02-REPLAY"), "case_id": "GC-SC02-FB001", "input_id": "TI-SC02-FB001"}
    test_input = {**case.pop("input"), "input_id": "TI-SC02-FB001", "variant": "feedback"}
    assert golden_dataset.add_case(case, test_input) == "golden-v2"
    assert golden_dataset.case("GC-SC02-FB001")["version_added"] == "golden-v2"
    assert golden_dataset.next_case_id("SC-02") == "GC-SC02-FB002"
    with pytest.raises(ValueError, match="already exists"):
        golden_dataset.add_case(case, test_input)


# ------------------------------------------------------------------- harness


def test_golden_run_is_isolated_and_scored(sc02, sandbox):
    result = deepeval_harness.run_golden_set(case_ids=SC02 + ["GC-SC02-MISSING"], use_judge=False, concurrency=1)
    agg = result["aggregate"]
    assert agg["cases"] == 3 and agg["failed_cases"] == 0 and agg["severity_match"] == 1.0
    assert agg["triage_class_match"] == 1.0 and agg["dispatch_routing_correct"] == 1.0
    assert agg["rca_top1"] == agg["rca_rubric_top1"] == 1.0  # no judge: the rubric is used
    missing = next(c for c in result["cases"] if c["case_id"] == "GC-SC02-MISSING")
    assert missing["metrics"]["abstention_correct"] == 0.0  # the scripted model never abstains
    assert store.list_incidents() == [] and outbox.read("pages") == []  # nothing in the live store or outbox
    assert store.load_output(result["cases"][0]["run_id"])["metadata"]["run_mode"] == "evaluation"
    assert deepeval_harness.load_result(result["eval_id"])["golden_dataset_version"] == "golden-v1"
    logged = [e for e in events(sandbox, "eval") if e["event"] == "eval_result"]
    assert logged[-1]["phase"] == "baseline" and logged[-1]["metrics"]["severity_match"] == 1.0


def test_evaluation_runs_never_deduplicate_against_live_incidents(sc02):
    live = run_investigation(scenario_id="SC-02")
    evaluated = run_investigation(scenario_id="SC-02", run_mode=RunMode.EVALUATION)
    assert live.dispatch.paged and not evaluated.dispatch.paged and evaluated.dispatch.suppressed
    assert [v.incident_id for v in store.list_incidents()] == [live.incident_id]
    assert len(outbox.read("pages")) == 1


def test_fast_path_is_kept_in_the_intended_dispatch(fake):
    fake("SC-01")
    out = run_investigation(scenario_id="SC-01", run_mode=RunMode.EVALUATION)
    assert out.dispatch.intended["page"]["fast_path"] is True and "page_update" in out.dispatch.intended


def test_programmatic_checks_on_a_system_error():
    output = {"status": "SYSTEM_ERROR", "rules_severity": {"level": "S1"}}
    checks = deepeval_harness.programmatic_checks(output, {"expected_severity": "S1"})
    assert checks == {"system_error": 1.0, "severity_match": 1.0}


class StubChat:
    """Answers any DeepEval schema with plausible values (for an offline run of the metric code)."""

    def with_structured_output(self, schema, method=None):
        return self

    def _build(self, schema):
        values = {}
        for name, field in schema.model_fields.items():
            values[name] = self._value(name, field.annotation)
        return schema(**values)

    def _value(self, name, annotation):
        origin = typing.get_origin(annotation)
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)) and args:
            return self._value(name, args[0])
        if origin is list:
            inner = args[0] if args else str
            return [self._value(name, inner)]
        if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
            return self._build(annotation)
        if annotation in (int, float):
            return 9
        if annotation is bool:
            return True
        return "yes" if "verdict" in name else "The payments backlog was caused by broker 2 failing."

    def invoke(self, prompt):
        schema = getattr(self, "_schema", None)
        return self._build(schema) if schema else type("Msg", (), {"content": "ok"})()


def test_deepeval_metrics_run_with_the_function_calling_judge(sc02, monkeypatch):
    stub = StubChat()

    def structured(schema, method=None):
        bound = StubChat()
        bound._schema = schema
        return bound

    stub.with_structured_output = structured
    judge = FunctionCallingJudge(chat_model=stub)
    out = run_investigation(scenario_id="SC-02", run_mode=RunMode.EVALUATION)
    metrics = deepeval_harness.deepeval_metrics(out.to_output_dict(), golden_dataset.case("GC-SC02-REPLAY"),
                                                store.load_retrieval(out.run_id), judge)
    assert set(metrics) >= {"rca_top1", "faithfulness", "hallucination", "contextual_recall"}
    assert all(v is None or 0.0 <= v <= 1.0 for v in metrics.values())


# ----------------------------------------------------------- SME promotion


def test_promote_adds_a_golden_case_and_discard_needs_a_reason(sc02, sandbox):
    reject_run("rev-a")
    reject_run("rev-b")
    first, second = feedback_loop.review_queue()
    truth = feedback_loop.default_ground_truth(first)
    assert truth["expected_issue_class"] == "eventhub_broker" and truth["expected_runbook_id"] == "RB-KFK-001"
    with pytest.raises(ValueError, match="only the SME"):
        feedback_loop.promote(first.candidate_id, truth, ESC, "rev-c")
    assert feedback_loop.promote(first.candidate_id, truth, SME, "sme-1") == "golden-v2"
    added = golden_dataset.case("GC-SC02-FB001")
    assert added["variant"] == "feedback" and added["source_feedback_id"] == first.feedback_id
    assert added["input"]["scenario_id"] == "SC-02" and added["expected_dispatch"]["route"] == "page"
    assert feedback_loop.get(first.candidate_id).golden_dataset_version_added == 2
    with pytest.raises(ValueError, match="reason"):
        feedback_loop.discard(second.candidate_id, " ", SME, "sme-1")
    feedback_loop.discard(second.candidate_id, "Duplicate of FC-0001", SME, "sme-1")
    assert feedback_loop.review_queue() == []
    logged = {e["event"] for e in events(sandbox)}
    assert {"feedback_candidate_promoted", "feedback_candidate_discarded"} <= logged


# ------------------------------------------------------------------ pairwise


def _second_version(agent: str = "rca_agent") -> str:
    return adaptation_engine._add_version(agent, {"type": "prompt_rule", "text": "Check the broker first."},
                                          "ADP-TEST")[1]


def test_pairwise_session_agreement_tie_break_win_rate_and_dpo(sc02, sandbox):
    v2 = _second_version()
    session = pairwise_session.create_session("rca_agent", "v1", v2, SC02, held_out_share=0.5, concurrency=1)
    assert session["status"] == "ready" and len(session["pairs"]) == 2
    assert sum(p["held_out"] for p in session["pairs"]) == 1
    p1, p2 = (p["pair_id"] for p in session["pairs"])
    left, right = pairwise_session.shown(session, p1)
    assert set(left) <= {"hypotheses", "timeline"} and set(right) <= {"hypotheses", "timeline"}
    b_side = {p["pair_id"]: ("left" if p["shown_order"][0] == "B" else "right") for p in session["pairs"]}
    a_side = {k: ("right" if v == "left" else "left") for k, v in b_side.items()}
    sid = session["session_id"]
    pairwise_session.label(sid, p1, b_side[p1], "clearer evidence", ESC, "r1")
    with pytest.raises(ValueError, match="already labelled"):
        pairwise_session.label(sid, p1, "tie", "again", ESC, "r1")
    pairwise_session.label(sid, p1, b_side[p1], "agree", ReviewerRole.PRODUCTION_SUPPORT, "r2")
    assert preference_store.pair_outcome(p1) == ("agreed", "B")
    pairwise_session.label(sid, p2, b_side[p2], "better", ESC, "r1")
    pairwise_session.label(sid, p2, a_side[p2], "worse", ESC, "r2")
    assert preference_store.agreement(p2) == "disputed"
    assert pairwise_session.next_pair_for(pairwise_session.load(sid), "sme-1", SME) == p2
    with pytest.raises(ValueError, match="only the SME"):
        pairwise_session.label(sid, p2, "tie", "x", ESC, "r3")
    pairwise_session.label(sid, p2, "tie", "both fine", SME, "sme-1")
    assert preference_store.pair_outcome(p2) == ("tie_broken", "tie")
    outcome = pairwise_session.summarize(sid)
    held_out = next(p for p in session["pairs"] if p["held_out"])["pair_id"]
    assert outcome["win_rate_b"] == (1.0 if held_out == p1 else 0.5) and outcome["agreement_rate"] == 0.5
    assert any(e["event"] == "pairwise_label_recorded" and e.get("stage") == "summary" for e in events(sandbox, "eval"))
    path, count = dpo_exporter.export()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert count == len(lines) == 1 and lines[0]["metadata"]["pair_id"] == p1
    assert lines[0]["metadata"]["chosen_version"] == f"rca_agent:{v2}" and lines[0]["chosen"].startswith("{")


def test_reward_score_components_and_sufficiency(sc02):
    out = run_investigation(scenario_id="SC-02")
    review_store.record_review(ReviewDecision(incident_id=out.incident_id, run_id=out.run_id, decision="approve",
                                              ratings=Ratings(rca=5, actions=5, severity=5, summary=1),
                                              reviewer_role=ESC, at=store.now()), "rev-a")
    reward = reward_model.compute(out.metadata.prompt_version_set)
    assert reward.components.mean_rating == pytest.approx(0.75) and reward.components.approval_rate == 1.0
    assert reward.components.win_rate is None and reward.components.fix_outcome_rate is None
    assert reward.score == pytest.approx((0.35 * 0.75 + 0.15 * 1.0) / 0.5) and reward.signal_count == 2
    assert not reward.sufficient
    assert reward_model.version_key(prompts.active_version_set()) in {r.prompt_version_set_key
                                                                     for r in reward_model.compute_all(log=False)}


def test_reviewer_share():
    assert preference_store.reviewer_share([]) == {}


# ---------------------------------------------------------------- adaptation


def root_cause_proposal(entries):
    """The proposal from the two root_cause_error rejections (their low ratings form other patterns)."""
    [entry] = [e for e in entries if e.status == "proposed" and e.trigger_pattern["kind"] == "root_cause_error"]
    return entry


def test_patterns_need_two_signals_from_different_reviewers(sc02):
    reject_run("rev-a")
    reject_run("rev-a")
    for c in feedback_loop.review_queue():
        feedback_loop.promote(c.candidate_id, feedback_loop.default_ground_truth(c), SME, "sme-1")
    [pattern] = [p for p in adaptation_engine.group_patterns(adaptation_engine.scan_signals())
                 if p["kind"] == "root_cause_error"]
    assert pattern["count"] == 2 and pattern["reviewer_cap_exceeded"] and not pattern["eligible"]
    entries = adaptation_engine.scan_and_propose(SME)
    assert any(e.status == "waiting" and e.trigger_pattern["kind"] == "root_cause_error" for e in entries)
    with pytest.raises(ValueError, match="SME or the Evaluation Engineer"):
        adaptation_engine.scan_and_propose(ESC)


def test_adaptation_reverts_when_the_target_does_not_improve(sc02, sandbox):
    reject_run("rev-a")
    reject_run("rev-b")
    for c in feedback_loop.review_queue():
        feedback_loop.promote(c.candidate_id, feedback_loop.default_ground_truth(c), SME, "sme-1")
    entry = root_cause_proposal(adaptation_engine.scan_and_propose(SME))
    assert entry.proposed_change["agent"] == "rca_agent" and entry.source == "feedback"
    with pytest.raises(ValueError, match="approved first"):
        adaptation_engine.apply(entry.adaptation_id, use_judge=False, case_ids=SC02)
    adaptation_engine.approve(entry.adaptation_id, EE, "ee-1", {"text": "Rank a broker failure above its symptoms."})
    done = adaptation_engine.apply(entry.adaptation_id, use_judge=False, case_ids=SC02)
    assert done.status == "reverted" and "did not improve" in done.explanation  # same scripted answers
    assert prompts.active_version_set()["rca_agent"] == "v1" and done.prompt_version_after == "v2"
    assert "v2" in prompts.load_versions()["rca_agent"]["versions"]  # kept as history, not active
    assert {"adaptation_reverted"} <= {e["event"] for e in events(sandbox)}


def test_full_evidence_chain_for_an_applied_adaptation(sc02, sandbox, tmp_path, monkeypatch):
    """T-ADAPT and T-EVIDENCE: E-1 to E-10 from the logs, then MISSING when a line is removed."""
    reject_run("rev-a")
    reject_run("rev-b")
    for c in feedback_loop.review_queue():
        feedback_loop.promote(c.candidate_id, feedback_loop.default_ground_truth(c), SME, "sme-1")
    entry = root_cause_proposal(adaptation_engine.scan_and_propose(SME))
    adaptation_engine.approve(entry.adaptation_id, SME, "sme-1")
    real_check = adaptation_engine.regression_check
    monkeypatch.setattr(adaptation_engine, "regression_check",
                        lambda b, a, t: {**real_check(b, a, t), "passed": True, "improved": True})
    done = adaptation_engine.apply(entry.adaptation_id, use_judge=False, case_ids=SC02)
    assert done.status == "applied" and prompts.active_version_set()["rca_agent"] == "v2"
    assert done.before_metrics and done.after_metrics and "Kept." in done.explanation
    _, missing = evidence_report.generate(done.adaptation_id, out_dir=tmp_path)
    assert missing == ["E-9", "E-10"]
    session = pairwise_session.create_session("rca_agent", "v1", "v2", SC02, held_out_share=1.0,
                                              adaptation_id=done.adaptation_id, concurrency=1)
    for p in session["pairs"]:
        for reviewer in ("r1", "r2"):
            pairwise_session.label(session["session_id"], p["pair_id"], "tie", "same", ESC, reviewer)
    pairwise_session.summarize(session["session_id"])
    later = run_investigation(scenario_id="SC-02")
    assert later.metadata.prompt_version_set["rca_agent"] == "v2"
    review_store.record_review(ReviewDecision(incident_id=later.incident_id, run_id=later.run_id, decision="approve",
                                              ratings=Ratings(rca=5, actions=4, severity=5, summary=4),
                                              reviewer_role=ESC, at=store.now()), "rev-a")
    reward_model.compute(later.metadata.prompt_version_set)
    path, missing = evidence_report.generate(done.adaptation_id, out_dir=tmp_path)
    assert missing == []
    text = path.read_text(encoding="utf-8")
    assert "| E-10 |" in text and "MISSING" not in text.split("## Before")[0]
    log = sandbox.logs_dir / "interactions.log"
    log.write_text("".join(line + "\n" for line in log.read_text(encoding="utf-8").splitlines()
                           if '"adaptation_approved"' not in line), encoding="utf-8")
    assert evidence_report.generate(done.adaptation_id, out_dir=tmp_path)[1] == ["E-6"]


def test_reject_and_scope_rules(sc02):
    reject_run("rev-a")
    reject_run("rev-b")
    for c in feedback_loop.review_queue():
        feedback_loop.promote(c.candidate_id, feedback_loop.default_ground_truth(c), SME, "sme-1")
    entry = root_cause_proposal(adaptation_engine.scan_and_propose(EE))
    with pytest.raises(ValueError, match="scope"):
        adaptation_engine.approve(entry.adaptation_id, SME, "sme-1", {"type": "severity_rules"})
    with pytest.raises(ValueError, match="reason"):
        adaptation_engine.reject(entry.adaptation_id, "", SME, "sme-1")
    with pytest.raises(ValueError, match="only the SME"):
        adaptation_engine.reject(entry.adaptation_id, "no", ESC, "e")
    assert adaptation_engine.reject(entry.adaptation_id, "Too vague", SME, "sme-1").status == "rejected"


def test_severity_signals_go_to_the_rules_review(sc02):
    from src.feedback import review_store as rs
    from src.schemas.enums import SeverityLevel
    from src.schemas.feedback import Reclassification

    out = run_investigation(scenario_id="SC-02")
    rs.record_reclassification(Reclassification(
        incident_id=out.incident_id, from_level=SeverityLevel.S2, to_level=SeverityLevel.S1, reason="Many customers",
        reviewer_role=ReviewerRole.PRODUCTION_SUPPORT, at=store.now()), "ps-1")
    summary = adaptation_engine.rules_review_summary()
    assert summary[0]["signal"] == "reclassification" and summary[0]["count"] == 1
    assert not [p for p in adaptation_engine.group_patterns(adaptation_engine.scan_signals())
                if "severity" in p["kind"]]


def test_fixture_sandbox_run_writes_nothing_to_the_live_outbox(fake, tmp_path):
    fake("SC-01")
    output, sent, incidents = deepeval_harness._sandbox_run("SC-01", tmp_path / "fixture-sandbox")
    assert output.dispatch.fast_path and len(sent["pages"]) == 2 and len(incidents) == 1
    assert outbox.read("pages") == [] and store.list_incidents() == []
    assert preference_store.query(SignalType.REVIEW) == []

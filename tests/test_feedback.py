"""Human steps after a run (Architecture Spec Sections 10.1, 10.2, 10.4; Req. 13.5 H-1 to H-7):
review with ratings, feedback candidates, high-risk confirmation, re-classification, page
acknowledgement, gate hand-off, resolution, verification and indexing, preference store."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.agent.core_agent import run_investigation
from src.feedback import feedback_loop, preference_store, resolution, review_store
from src.schemas.enums import (FixOutcome, IncidentState, InvestigationStatus, IssueType, ReviewerRole,
                               SeverityLevel, SignalType)
from src.schemas.feedback import (Ratings, Reclassification, ResolutionRecord, ReviewDecision,
                                  VerificationRecord)
from src.services import incident_state_store as store
from src.tools.dispatch_mocks import outbox

ESC, PS, SME = ReviewerRole.ESCALATION, ReviewerRole.PRODUCTION_SUPPORT, ReviewerRole.SME
RATINGS = Ratings(rca=4, actions=3, severity=5, summary=4)


@pytest.fixture
def run(fake):
    """Run a scenario with the scripted model and return its output."""
    def go(scenario_id: str = "SC-02", **oracle):
        fake(scenario_id if scenario_id.startswith("SC") else "SC-01", **oracle)
        return run_investigation(scenario_id=scenario_id)
    return go


def decision(out, kind: str, **fields) -> ReviewDecision:
    return ReviewDecision(incident_id=out.incident_id, run_id=out.run_id, decision=kind, reviewer_role=ESC,
                          at=store.now(), **fields)


def events(sandbox) -> list[dict]:
    path = sandbox.logs_dir / "interactions.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_approve_records_status_ratings_and_hash(run, sandbox):
    out = run()
    result = review_store.record_review(decision(out, "approve", ratings=RATINGS), "rev-a")
    assert result["status"] == InvestigationStatus.APPROVED and result["candidate_id"] is None
    view = store.current(out.incident_id)
    review = view.reviews[out.run_id]
    assert review["status"] == "APPROVED" and review["ratings"]["severity"] == 5
    assert review["content_hash"] == review_store.content_hash(store.load_output(out.run_id))
    assert review_store.review_status(out.incident_id, out.run_id) == "APPROVED"
    assert view.state == IncidentState.PAGED  # a review does not change the lifecycle state
    prefs = preference_store.query(run_id=out.run_id)
    assert {p.signal_type for p in prefs} == {SignalType.REVIEW, SignalType.RATING}
    assert all(p.prompt_version_set and p.scenario_id == "SC-02" and p.integrity.pii_checked for p in prefs)
    logged = {e["event"] for e in events(sandbox)}
    assert {"review_decision", "rating_recorded"} <= logged
    with pytest.raises(ValueError, match="already reviewed"):
        review_store.record_review(decision(out, "approve"), "rev-b")


def test_edit_and_reject_need_details_and_become_candidates(run):
    out = run()
    with pytest.raises(ValidationError):
        decision(out, "reject", reason="wrong")  # issue type and ratings missing
    with pytest.raises(ValueError, match="only lower"):
        review_store.record_review(decision(out, "edit", reason="r", issue_type=IssueType.SEVERITY_ERROR,
                                            ratings=RATINGS, edited_output={"severity": "S1"}), "rev-a")
    with pytest.raises(ValueError, match="cannot be edited"):
        review_store.record_review(decision(out, "edit", reason="r", issue_type=IssueType.VERBOSITY,
                                            ratings=RATINGS, edited_output={"incident_state": "OPEN"}), "rev-a")
    result = review_store.record_review(decision(
        out, "edit", reason="Lag is a symptom; call jane.doe@example.com", issue_type=IssueType.ROOT_CAUSE_ERROR,
        ratings=RATINGS, edited_output={"severity": "S3", "root_cause": "Broker 2 disk failure"}), "rev-a")
    assert result["status"] == InvestigationStatus.EDITED and result["candidate_id"] == "FC-0001"
    [candidate] = feedback_loop.review_queue()
    assert candidate.status == "pending" and candidate.issue_type == IssueType.ROOT_CAUSE_ERROR
    assert "jane.doe@example.com" not in candidate.model_dump_json()
    assert "Broker 2 disk failure" in candidate.reviewer_correction
    assert candidate.original_output["run_id"] == out.run_id and candidate.original_incident["run_id"] == out.run_id
    assert store.current(out.incident_id).reviews[out.run_id]["candidate_id"] == "FC-0001"


def test_reject_creates_a_candidate(run):
    out = run("SC-05")
    result = review_store.record_review(decision(out, "reject", reason="Wrong service",
                                                 issue_type=IssueType.CITATION_ERROR, ratings=RATINGS), "rev-a")
    assert result["status"] == InvestigationStatus.REJECTED
    assert feedback_loop.candidates()[0].reviewer_correction == "Rejected: Wrong service"


def test_system_error_run_cannot_be_reviewed(run):
    out = run("FX-LLM-DOWN")
    assert out.status == InvestigationStatus.SYSTEM_ERROR
    with pytest.raises(ValueError, match="SYSTEM_ERROR"):
        review_store.record_review(decision(out, "approve"), "rev-a")


def test_high_risk_steps_need_two_step_confirmation(run, sandbox):
    out = run()
    path = sandbox.incident_state_dir / "outputs" / f"{out.run_id}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["recommended_actions"][-1]["risk_level"] = "high"
    path.write_text(json.dumps(saved), encoding="utf-8")
    step = saved["recommended_actions"][-1]["step"]
    with pytest.raises(ValueError, match="high-risk"):
        review_store.record_review(decision(out, "approve"), "rev-a")
    review_store.record_review(decision(out, "approve", high_risk_confirmations=[step]), "rev-a")
    assert store.current(out.incident_id).reviews[out.run_id]["high_risk_confirmations"] == [step]


def test_reclassification_by_production_support_pages(run, sandbox):
    out = run()  # SC-02 is S2
    record = Reclassification(incident_id=out.incident_id, from_level=SeverityLevel.S2, to_level=SeverityLevel.S1,
                              reason="Balances wrong for many customers", reviewer_role=PS, at=store.now())
    with pytest.raises(ValueError, match="Production Support"):
        review_store.record_reclassification(record.model_copy(update={"reviewer_role": ESC}), "rev-a")
    with pytest.raises(ValueError, match="raise"):
        review_store.record_reclassification(record.model_copy(update={"to_level": SeverityLevel.S2}), "rev-a")
    dispatch = review_store.record_reclassification(record, "rev-a")
    assert dispatch.paged and [p["mode"] for p in outbox.read("pages")] == ["new", "update"]
    view = store.current(out.incident_id)
    assert view.reclassifications[0]["to_level"] == "S1" and view.state == IncidentState.PAGED
    assert preference_store.query(SignalType.RECLASSIFICATION)[0].payload["from_level"] == "S2"
    assert "reclassified" in {e["event"] for e in events(sandbox)}


def test_page_acknowledgement_records_time_to_acknowledge(run, sandbox):
    out = run("SC-01")
    with pytest.raises(ValueError, match="acknowledged by"):
        review_store.record_acknowledgement(out.incident_id, SME, "rev-a")
    result = review_store.record_acknowledgement(out.incident_id, ESC, "rev-a")
    view = store.current(out.incident_id)
    assert view.acknowledged_at == result["acknowledged_at"] and result["seconds_to_acknowledge"] >= 0
    with pytest.raises(ValueError, match="already acknowledged"):
        review_store.record_acknowledgement(out.incident_id, ESC, "rev-a")
    assert "page_acknowledged" in {e["event"] for e in events(sandbox)}


def test_gate_handoff_only_for_handed_off_runs(run):
    out = run("SC-02", confidence=0.4)
    assert out.needs_human_rca
    feedback_id = review_store.record_gate_handoff(out.incident_id, out.run_id, True, 2, ESC, "rev-a", "rank 2")
    [pref] = preference_store.query(SignalType.GATE_HANDOFF)
    assert pref.feedback_id == feedback_id and pref.payload["rank_found"] == 2
    assert store.current(out.incident_id).gate_handoffs[out.run_id]["found_in_ranked_list"] is True
    with pytest.raises(ValueError, match="already recorded"):
        review_store.record_gate_handoff(out.incident_id, out.run_id, False, None, ESC, "rev-a")
    other = run("SC-05")
    with pytest.raises(ValueError, match="not handed off"):
        review_store.record_gate_handoff(other.incident_id, other.run_id, True, 1, ESC, "rev-a")


def resolve(out, outcome=FixOutcome.WORKED):
    resolution.record_resolution(ResolutionRecord(
        incident_id=out.incident_id, actual_root_cause="Broker 2 failed and partitions were under-replicated",
        actions_taken="Replaced broker 2 and rebalanced", fix_outcome=outcome, resolution_time_min=42,
        resolver_role=ESC, at=store.now()), "rev-a")


def test_resolution_and_confirmed_verification_index_the_incident(run, sandbox):
    out = run()
    with pytest.raises(ValueError, match="no resolution"):
        resolution.record_verification(VerificationRecord(incident_id=out.incident_id, verdict="confirmed",
                                                          verifier_role=SME, at=store.now()), "rev-b")
    resolve(out)
    assert store.current(out.incident_id).state == IncidentState.RESOLVED
    with pytest.raises(ValueError, match="already has a resolution"):
        resolve(out)
    result = resolution.record_verification(VerificationRecord(
        incident_id=out.incident_id, verdict="corrected", corrected_root_cause="Broker 2 disk failure",
        verifier_role=SME, at=store.now()), "rev-b")
    assert result["indexed"] and result["state"] == IncidentState.CLOSED_VERIFIED
    view = store.current(out.incident_id)
    assert view.state == IncidentState.CLOSED_VERIFIED and view.indexed_doc_id == f"VR-{out.incident_id}"
    text = (sandbox.verified_resolutions_dir / f"VR-{out.incident_id}.md").read_text(encoding="utf-8")
    assert "Broker 2 disk failure" in text
    assert {p.signal_type for p in preference_store.query(incident_id=out.incident_id)} == {
        SignalType.FIX_OUTCOME, SignalType.VERIFICATION}
    logged = {e["event"] for e in events(sandbox)}
    assert {"resolution_recorded", "root_cause_verified", "resolution_indexed"} <= logged


def test_rejected_verification_closes_unverified_without_indexing(run, sandbox):
    out = run()
    resolve(out, FixOutcome.DID_NOT_WORK)
    result = resolution.record_verification(VerificationRecord(incident_id=out.incident_id, verdict="rejected",
                                                               verifier_role=ESC, at=store.now()), "rev-b")
    assert not result["indexed"] and result["state"] == IncidentState.CLOSED_UNVERIFIED
    assert store.current(out.incident_id).indexed_doc_id is None
    assert not (sandbox.verified_resolutions_dir / f"VR-{out.incident_id}.md").exists()


def test_failed_indexing_keeps_the_verification_and_can_be_retried(run):
    out = run()
    resolve(out)

    def broken(incident_id):
        raise RuntimeError("embedding service down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(resolution.resolution_indexer, "index_verified", broken)
        result = resolution.record_verification(VerificationRecord(
            incident_id=out.incident_id, verdict="confirmed", verifier_role=SME, at=store.now()), "rev-b")
    assert not result["indexed"] and "could not be indexed" in result["index_error"]
    assert store.current(out.incident_id).state == IncidentState.CLOSED_VERIFIED
    assert resolution.retry_indexing(out.incident_id) == f"VR-{out.incident_id}"
    assert store.current(out.incident_id).indexed_doc_id == f"VR-{out.incident_id}"


def test_preference_store_marks_duplicates_and_redacts(sandbox):
    ctx = {"incident_id": "INC-20260314-001", "run_id": "RUN-1", "scenario_id": "SC-01"}
    first = preference_store.append(preference_store.make(SignalType.REVIEW, ctx, ESC, "rev-a",
                                                          {"reason": "mail me at a.b@example.com"}, store.now()))
    second = preference_store.append(preference_store.make(SignalType.REVIEW, ctx, ESC, "rev-a", {}, store.now()))
    other = preference_store.append(preference_store.make(SignalType.REVIEW, ctx, ESC, "rev-b", {}, store.now()))
    assert "a.b@example.com" not in first.payload["reason"] and first.integrity.pii_checked
    assert second.integrity.duplicate_of == first.feedback_id and other.integrity.duplicate_of is None
    assert [p.feedback_id for p in preference_store.query(SignalType.REVIEW)] == [first.feedback_id,
                                                                                  other.feedback_id]
    assert len(preference_store.query(include_duplicates=True)) == 3


def test_archive_state_moves_incidents_aside(run, sandbox):
    out = run()
    target = store.archive_state()
    assert (target / "incidents.jsonl").exists() and (target / "outputs" / f"{out.run_id}.json").exists()
    assert store.list_incidents() == [] and store.archive_state() is None

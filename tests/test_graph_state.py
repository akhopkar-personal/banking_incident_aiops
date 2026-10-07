"""Graph state reducers, alone and inside a real LangGraph run (Architecture Spec 3.14)."""

from __future__ import annotations

import pytest
from langgraph.graph import END, START, StateGraph

from src.schemas.analysis import RetrievedDocument
from src.schemas.enums import RunMode
from src.schemas.evidence import EvidenceItem
from src.schemas.graph_state import (
    GraphState,
    append_unique_documents,
    initial_state,
    merge_dispatch,
    sum_by_key,
)
from src.schemas.output import DispatchRecord, Notification


def doc(doc_id, section="1"):
    return RetrievedDocument(doc_id=doc_id, section=section, title=doc_id, doc_type="runbook", score=0.9, excerpt="x")


def test_append_unique_documents():
    merged = append_unique_documents([doc("RB-1"), doc("RB-2")], [doc("RB-1"), doc("RB-1", "2")])
    assert [(d.doc_id, d.section) for d in merged] == [("RB-1", "1"), ("RB-2", "1"), ("RB-1", "2")]


def test_sum_by_key():
    assert sum_by_key({"triage": 100}, {"triage": 50, "rca": 10}) == {"triage": 150, "rca": 10}


def test_merge_dispatch_keeps_fast_path_page(t0):
    first = DispatchRecord(paged=True, fast_path=True, page_time=t0)
    later = DispatchRecord(itsm_ticket_id="MOCK-ITSM-0001", assigned_queue="incident-escalation",
                           notifications=[Notification(channel="chat", audience="#inc", time=t0, outbox_id="OB-1")])
    merged = merge_dispatch(first, later)
    assert merged.paged and merged.fast_path and merged.page_time == t0
    assert merged.itsm_ticket_id == "MOCK-ITSM-0001"
    again = merge_dispatch(merged, DispatchRecord(notifications=merged.notifications))
    assert len(again.notifications) == 1


def test_initial_state_rejects_override_in_live_mode():
    initial_state("x", run_mode=RunMode.EVALUATION, prompt_version_override={"rca_agent": "v1"})
    with pytest.raises(ValueError, match="evaluation run mode"):
        initial_state("x", prompt_version_override={"rca_agent": "v1"})


def test_parallel_nodes_merge_state_in_langgraph(t0):
    """Two nodes in the same step write evidence, documents, dispatch and token usage;
    the reducers must combine both writes."""

    def rca(state: GraphState) -> dict:
        return {
            "evidence": {"LOG-1": EvidenceItem(evidence_id="LOG-1", source="LOG", service="s", timestamp=t0, summary="a")},
            "retrieved_documents": [doc("RB-PAY-003")],
            "token_usage": {"rca_agent": 120},
            "guardrail_flags": ["stale_citation"],
        }

    def changes(state: GraphState) -> dict:
        return {
            "evidence": {"DEP-7": EvidenceItem(evidence_id="DEP-7", source="DEP", service="s", timestamp=t0, summary="b")},
            "retrieved_documents": [doc("RB-PAY-003"), doc("PM-2025-011")],
            "token_usage": {"change_correlation_agent": 80},
            "dispatch": DispatchRecord(itsm_ticket_id="MOCK-ITSM-0001"),
        }

    def fast_page(state: GraphState) -> dict:
        return {"dispatch": DispatchRecord(paged=True, fast_path=True, page_time=t0)}

    graph = StateGraph(GraphState)
    graph.add_node("fast_page", fast_page)
    graph.add_node("rca", rca)
    graph.add_node("changes", changes)
    graph.add_edge(START, "fast_page")
    graph.add_edge(START, "rca")
    graph.add_edge(START, "changes")
    graph.add_edge(["fast_page", "rca", "changes"], END)
    result = graph.compile().invoke(initial_state("payments failing"))

    assert set(result["evidence"]) == {"LOG-1", "DEP-7"}
    assert [d.doc_id for d in result["retrieved_documents"]] == ["RB-PAY-003", "PM-2025-011"]
    assert result["token_usage"] == {"rca_agent": 120, "change_correlation_agent": 80}
    assert result["guardrail_flags"] == ["stale_citation"]
    assert result["dispatch"].paged and result["dispatch"].itsm_ticket_id == "MOCK-ITSM-0001"

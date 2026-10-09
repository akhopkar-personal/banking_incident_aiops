"""Knowledge-base retrieval (Architecture Spec Section 8; Req. FR-55, FR-56). T-RETRIEVAL, T-VERIFY."""

from __future__ import annotations

import json
import shutil
from datetime import date

import pytest

from src.config import REPO_ROOT
from src.schemas.enums import IncidentState
from src.services import incident_state_store as store
from src.tool_retrieval import chunker, document_loader, reindex_job, resolution_indexer, retriever

RAW = REPO_ROOT / "knowledge" / "raw"


def test_loader_reads_all_documents_including_pdf_headings(sandbox):
    docs = {d.doc_id: d for d in document_loader.load_documents()}
    assert len([d for d in docs if not d.startswith("VR-")]) == 28
    pdf = docs["PM-2025-019"]
    assert pdf.doc_type == "postmortem" and pdf.last_verified == date(2026, 8, 5)
    assert "\n## 4. Root cause\n" in pdf.text and pdf.text.startswith("# PM-2025-019")


def test_chunks_keep_section_metadata_and_size(sandbox):
    docs = {d.doc_id: d for d in document_loader.load_documents()}
    chunks = chunker.chunk_document(docs["RB-PAY-003"])
    assert {c.section for c in chunks} >= {"1. When to use this runbook", "2. Immediate mitigation: roll back"}
    assert all(len(c.text) <= sandbox.chunk_size for c in chunks)
    assert all(c.doc_id == "RB-PAY-003" and c.citation_status == "verified" for c in chunks)


def test_search_filters_and_staleness(sandbox):
    results = retriever.search("traffic throttling load shedding priorities", top_k=5, today=date(2026, 10, 9))
    by_doc = {r.doc_id: r for r in results}
    assert by_doc["RB-GEN-002"].stale and not any(r.stale for r in results if r.doc_id != "RB-GEN-002")
    only_regulatory = retriever.search("reporting deadline for data integrity incidents", top_k=3,
                                       doc_types=["regulatory"])
    assert only_regulatory and {r.doc_type for r in only_regulatory} == {"regulatory"}
    assert max(sum(1 for r in results if r.doc_id == d) for d in by_doc) <= retriever.MAX_SECTIONS_PER_DOC


def test_unverified_documents_are_excluded_unless_requested(sandbox, tmp_path):
    root = tmp_path / "raw"
    shutil.copytree(RAW / "runbooks", root / "runbooks")
    (root / "postmortems" / "auto").mkdir(parents=True)
    (root / "postmortems" / "auto" / "AUTO-1.md").write_text(
        "---\ndoc_id: AUTO-1\ntitle: Draft postmortem zebra quokka\ndoc_type: auto_postmortem\n"
        "last_verified: 2026-09-01\ncitation_status: unverified\n---\n\n# AUTO-1\n\n## 1. Summary\n\n"
        "zebra quokka outage summary\n", encoding="utf-8")
    index = tmp_path / "index"
    reindex_job.rebuild(root, index)
    assert "AUTO-1" not in {r.doc_id for r in retriever.search("zebra quokka outage", index_dir=index)}
    found = retriever.search("zebra quokka outage", include_unverified=True, index_dir=index)
    assert found[0].doc_id == "AUTO-1" and found[0].citation_status == "unverified"


def test_aliases_expand_queries(sandbox, tmp_path):
    aliases = {"cert": ["certificate expired TLS handshake renewal"]}
    assert retriever.expand_query("cert on card route", aliases) == "cert on card route certificate expired TLS handshake renewal"
    assert retriever.expand_query("broker down", aliases) == "broker down"


def test_reindex_detects_changes(sandbox, tmp_path):
    root = tmp_path / "raw"
    shutil.copytree(RAW / "regulatory", root / "regulatory")
    index = tmp_path / "index"
    first = reindex_job.run_reindex(root, index)
    assert first["rebuilt"] and sorted(first["added"]) == ["REG-001", "REG-002"]
    assert not reindex_job.run_reindex(root, index)["rebuilt"]
    path = root / "regulatory" / "REG-002_customer_communication_sla.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nOne more line.\n", encoding="utf-8")
    report = reindex_job.run_reindex(root, index)
    assert report["changed"] == ["REG-002"] and report["rebuilt"]
    (root / "regulatory" / "REG-001_incident_reporting_obligations.pdf").unlink()
    assert reindex_job.run_reindex(root, index)["removed"] == ["REG-001"]


def test_verified_resolution_is_indexed_and_retrievable(sandbox, tmp_path):
    index = tmp_path / "index"
    shutil.copytree(sandbox.faiss_index_dir, index)
    sandbox.faiss_index_dir = index
    retriever.clear_cache()
    incident = "INC-20260314-001"
    store.append(incident, "RUN-1", "created", IncidentState.OPEN, "correlate_dedup",
                 {"idempotency_key": "k1", "root_service": "payments-service"})
    store.append(incident, "RUN-1", "resolved", IncidentState.RESOLVED, "production_support",
                 {"actual_root_cause": "Release v2.14.0 cut the DB client timeout to 1 s (zebra marker)",
                  "actions_taken": "Rolled back to v2.13.4; call 07700 900123 for details", "fix_outcome": "worked"})
    with pytest.raises(ValueError, match="no confirmed or corrected verification"):
        resolution_indexer.index_verified(incident)
    store.append(incident, "RUN-1", "verified", IncidentState.CLOSED_VERIFIED, "sme", {"verdict": "confirmed"})
    path = resolution_indexer.index_verified(incident)
    assert path.name == "VR-INC-20260314-001.md" and "900123" not in path.read_text(encoding="utf-8")
    results = retriever.search("release cut the DB client timeout zebra marker", top_k=3,
                               doc_types=["verified_resolution"])
    assert results and results[0].doc_id == "VR-INC-20260314-001"
    assert store.current(incident).events[-1] == "indexed"


@pytest.mark.llm
def test_expected_runbook_retrieved_with_openai_embeddings():
    """T-RETRIEVAL against the real index (python scripts/build_index.py). Makes one embedding call per query."""
    from data.generators import SCENARIOS
    from src import config

    if config.get_settings().openai_api_key is None:
        pytest.skip("OPENAI_API_KEY is not set")
    retriever.clear_cache()
    for s in SCENARIOS.values():
        by_cause = [r.doc_id for r in retriever.search(s.root_cause, top_k=5)]
        runbooks = [r.doc_id for r in retriever.search(s.free_text, top_k=5, doc_types=["runbook", "verified_resolution"])]
        any_doc = [r.doc_id for r in retriever.search(s.free_text, top_k=5)]
        assert s.runbook_id in by_cause and s.runbook_id in runbooks, s.scenario_id
        assert set(s.expected_doc_ids) & set(any_doc), s.scenario_id

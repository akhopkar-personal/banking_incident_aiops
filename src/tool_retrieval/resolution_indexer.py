"""Index a human-verified resolution into the knowledge base (Architecture Spec
Section 8; Req. FR-55). Only `confirmed` or `corrected` verifications are
indexed; a rejected or missing verification is refused."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src import config
from src.logger_setup import log_interaction
from src.safety.redaction import redact_text
from src.schemas.enums import IncidentState
from src.services import incident_state_store as store

from . import embedder, faiss_store
from .chunker import chunk_document
from .document_loader import load_document


def index_verified(incident_id: str, today: date | None = None) -> Path:
    view = store.current(incident_id)
    if view is None:
        raise ValueError(f"unknown incident {incident_id}")
    verification, resolution = view.verification or {}, view.resolution or {}
    if verification.get("verdict") not in ("confirmed", "corrected"):
        raise ValueError(f"{incident_id} has no confirmed or corrected verification; it cannot be indexed (FR-55)")
    if not resolution:
        raise ValueError(f"{incident_id} has no resolution record")
    root_cause = (verification.get("corrected_root_cause") if verification["verdict"] == "corrected"
                  else resolution.get("actual_root_cause"))
    doc_id = f"VR-{incident_id}"
    text = "\n".join([
        "---", f"doc_id: {doc_id}", f"title: Verified resolution for {incident_id}", "doc_type: verified_resolution",
        f"services: {view.root_service or ''}", f"last_verified: {(today or date.today()).isoformat()}",
        "citation_status: verified", "source: human_verified", "---", "",
        f"# {doc_id}: verified resolution", "",
        "## 1. Incident", "",
        f"Incident {incident_id} on {view.root_service or 'an unrecorded service'}, created "
        f"{view.created_at:%Y-%m-%d %H:%M} UTC.", "",
        "## 2. Verified root cause", "", redact_text(root_cause or ""), "",
        "## 3. Actions taken and outcome", "",
        redact_text(resolution.get("actions_taken", "")), "",
        f"Fix outcome: {resolution.get('fix_outcome', 'not recorded')}. Verification: {verification['verdict']}.", "",
    ])
    settings = config.get_settings()
    folder = settings.verified_resolutions_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{doc_id}.md"
    path.write_text(text, encoding="utf-8", newline="\n")

    doc = load_document(path, settings.knowledge_dir / "raw")
    chunks = chunk_document(doc)
    vectors = embedder.embed_texts([c.embedding_text() for c in chunks])
    faiss_store.add_chunks([c.to_meta() for c in chunks], vectors, doc_hashes={doc.doc_id: doc.content_hash})
    run_id = view.run_ids[-1]
    store.append(incident_id, run_id, "indexed", view.state if view.state else IncidentState.CLOSED_VERIFIED,
                 "resolution_indexer", {"doc_id": doc_id, "chunks": len(chunks)})
    log_interaction("resolution_indexed", component="resolution_indexer", incident_id=incident_id, run_id=run_id,
                    doc_id=doc_id, chunks=len(chunks))
    return path

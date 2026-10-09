"""Build and re-index the knowledge base (Architecture Spec Section 8; Req. FR-56).

`rebuild` embeds every document and writes a new index. `run_reindex` compares
content hashes with the index metadata and rebuilds only if something was
added, changed or removed; unchanged chunks come from the embedding cache, so a
rebuild after a small edit costs only the changed chunks.

CLI: python -m src.tool_retrieval.reindex_job [--force]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from src import config
from src.logger_setup import log_eval

from . import embedder, faiss_store
from .chunker import chunk_document
from .document_loader import load_documents


def rebuild(root: Optional[Path] = None, index_dir: Optional[Path] = None) -> dict[str, Any]:
    docs = load_documents(root)
    chunks = [c for d in docs for c in chunk_document(d)]
    vectors = embedder.embed_texts([c.embedding_text() for c in chunks])
    faiss_store.build_index([c.to_meta() for c in chunks], vectors, model=embedder.model_name(),
                            doc_hashes={d.doc_id: d.content_hash for d in docs}, index_dir=index_dir)
    return {"documents": len(docs), "chunks": len(chunks), "model": embedder.model_name()}


def run_reindex(root: Optional[Path] = None, index_dir: Optional[Path] = None, force: bool = False) -> dict[str, Any]:
    docs = {d.doc_id: d.content_hash for d in load_documents(root)}
    try:
        _, meta = faiss_store.load_index(index_dir)
        indexed, model = meta.get("doc_hashes", {}), meta.get("model")
    except FileNotFoundError:
        indexed, model = {}, None
    report = {
        "added": sorted(set(docs) - set(indexed)),
        "removed": sorted(set(indexed) - set(docs)),
        "changed": sorted(d for d in set(docs) & set(indexed) if docs[d] != indexed[d]),
        "model_changed": model != embedder.model_name(),
    }
    report["unchanged"] = len(docs) - len(report["added"]) - len(report["changed"])
    report["rebuilt"] = bool(force or report["added"] or report["removed"] or report["changed"]
                             or report["model_changed"])
    if report["rebuilt"]:
        report.update(rebuild(root, index_dir))
    log_eval("knowledge_index_built", component="reindex_job",
             **{k: v for k, v in report.items() if k != "unchanged"}, unchanged_documents=report["unchanged"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-index changed knowledge-base documents")
    parser.add_argument("--force", action="store_true", help="rebuild even if nothing changed")
    args = parser.parse_args()
    config.get_settings().apply_to_environment()
    print(json.dumps(run_reindex(force=args.force), indent=2))


if __name__ == "__main__":
    main()

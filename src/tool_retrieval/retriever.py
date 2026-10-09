"""Semantic search over the knowledge base (Architecture Spec Section 8).

Query expansion from knowledge/retrieval_aliases.json; filter by document type;
unverified documents excluded unless asked for; documents whose last_verified
date is older than STALE_DOC_DAYS are marked stale.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from src import config
from src.errors import RecoverableError
from src.safety.output_checks import is_stale
from src.schemas.analysis import RetrievedDocument

from . import embedder, faiss_store

EXCERPT_CHARS = 700
# At most this many sections of one document in a result list, so one long document
# cannot crowd out the others.
MAX_SECTIONS_PER_DOC = 2
_lock = threading.Lock()
_loaded: dict[str, tuple[float, object, dict]] = {}


def load_aliases(path: Optional[Path] = None) -> dict[str, list[str]]:
    path = path or config.get_settings().knowledge_dir / "retrieval_aliases.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("aliases", {})


def expand_query(query: str, aliases: Optional[dict[str, list[str]]] = None) -> str:
    aliases = load_aliases() if aliases is None else aliases
    extra = [term for key, terms in aliases.items() if key.lower() in query.lower() for term in terms]
    return " ".join([query, *extra]) if extra else query


def _index(index_dir: Optional[Path]):
    directory = Path(index_dir) if index_dir else config.get_settings().faiss_index_dir
    key = str(directory)
    mtime = faiss_store.index_mtime(directory)
    with _lock:
        cached = _loaded.get(key)
        if cached is None or cached[0] != mtime:
            try:
                index, meta = faiss_store.load_index(directory)
            except FileNotFoundError as exc:
                raise RecoverableError(str(exc)) from exc
            cached = (mtime, index, meta)
            _loaded[key] = cached
        return cached[1], cached[2]


def search(query: str, top_k: int = 5, doc_types: Optional[Sequence[str]] = None, include_unverified: bool = False,
           index_dir: Optional[Path] = None, today: Optional[date] = None) -> list[RetrievedDocument]:
    """Top `top_k` chunks for the query, best first, at most one per document section."""
    index, meta = _index(index_dir)
    if meta["model"] != embedder.model_name():
        raise RecoverableError(f"index was built with {meta['model']} but the embedder is {embedder.model_name()}; "
                               f"rebuild the index")
    vector = embedder.embed_query(expand_query(query)).reshape(1, -1).astype(np.float32)
    scores, ids = index.search(vector, index.ntotal)
    stale_days = config.get_settings().stale_doc_days
    results: list[RetrievedDocument] = []
    seen: set[tuple[str, Optional[str]]] = set()
    per_doc: dict[str, int] = {}
    for score, idx in zip(scores[0], ids[0]):
        if idx < 0:
            continue
        chunk = meta["chunks"][idx]
        if doc_types and chunk["doc_type"] not in doc_types:
            continue
        if chunk["citation_status"] != "verified" and not include_unverified:
            continue
        key = (chunk["doc_id"], chunk["section"])
        if key in seen or per_doc.get(chunk["doc_id"], 0) >= MAX_SECTIONS_PER_DOC:
            continue
        seen.add(key)
        per_doc[chunk["doc_id"]] = per_doc.get(chunk["doc_id"], 0) + 1
        verified_on = date.fromisoformat(chunk["last_verified"]) if chunk.get("last_verified") else None
        results.append(RetrievedDocument(
            doc_id=chunk["doc_id"], section=chunk["section"], title=chunk["title"], doc_type=chunk["doc_type"],
            score=round(float(score), 4), excerpt=chunk["text"][:EXCERPT_CHARS],
            citation_status="verified" if chunk["citation_status"] == "verified" else "unverified",
            last_verified=verified_on, stale=is_stale(verified_on, stale_days, today),
        ))
        if len(results) >= top_k:
            break
    return results


def clear_cache() -> None:
    with _lock:
        _loaded.clear()

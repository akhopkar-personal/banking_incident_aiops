"""search_knowledge_base (Architecture Spec Section 7.3): FAISS search through
the retriever. Excerpts were redacted when the index was built."""

from __future__ import annotations

from typing import Optional, Sequence

from src.schemas.analysis import RetrievedDocument


def search_knowledge_base(query: str, top_k: int = 5, doc_types: Optional[Sequence[str]] = None,
                          include_unverified: bool = False) -> list[RetrievedDocument]:
    from src.tool_retrieval import retriever  # FAISS is imported on first search, not at server start

    return retriever.search(query, top_k=max(1, min(int(top_k), 10)), doc_types=list(doc_types) if doc_types else None,
                            include_unverified=include_unverified)

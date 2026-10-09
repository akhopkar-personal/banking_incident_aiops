"""Split documents into chunks (Architecture Spec Section 8): by Markdown
heading first, so every chunk knows its section, then by size."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from src import config
from src.safety.redaction import redact_text

from .document_loader import Document


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    doc_type: str
    section: Optional[str]
    last_verified: Optional[str]
    citation_status: str
    path: str
    text: str

    def embedding_text(self) -> str:
        """Title and section are prepended so a chunk can match on them too."""
        return f"{self.title}\n{self.section or ''}\n{self.text}".strip()

    def to_meta(self) -> dict:
        return asdict(self)


def chunk_document(doc: Document, chunk_size: Optional[int] = None, overlap: Optional[int] = None) -> list[Chunk]:
    settings = config.get_settings()
    size = chunk_size or settings.chunk_size
    over = settings.chunk_overlap if overlap is None else overlap
    by_heading = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "h1"), ("##", "h2")], strip_headers=True)
    by_size = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=over)
    chunks: list[Chunk] = []
    for part in by_heading.split_text(doc.text):
        section = part.metadata.get("h2")
        for piece in by_size.split_text(part.page_content):
            if not piece.strip():
                continue
            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}#{len(chunks)}", doc_id=doc.doc_id, title=doc.title, doc_type=doc.doc_type,
                section=section, last_verified=doc.last_verified.isoformat() if doc.last_verified else None,
                citation_status=doc.citation_status, path=doc.path,
                text=redact_text(piece.strip()),  # documents are scrubbed before embedding (Req. 13.3)
            ))
    return chunks

"""Load knowledge-base documents (Architecture Spec Section 8).

Markdown and text documents carry their metadata in a front matter block;
PDFs carry it in the PDF Info dictionary, and their headings (set in bold) are
turned back into Markdown headings so the chunker can split by section.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src import config

REQUIRED_KEYS = ("doc_id", "title", "doc_type", "last_verified", "citation_status")
SUPPORTED = {".md", ".txt", ".pdf", ".json"}


@dataclass
class Document:
    doc_id: str
    title: str
    doc_type: str
    last_verified: Optional[date]
    citation_status: str
    text: str  # Markdown body, without front matter
    path: str  # relative to the knowledge root
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        payload = json.dumps({"text": self.text, "meta": {k: str(v) for k, v in sorted(self.meta.items())}},
                             sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    header, _, body = text[4:].partition("\n---\n")
    meta = {}
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"')
    return meta, body.lstrip("\n")


def _pdf(path: Path) -> tuple[dict[str, str], str]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        info = pdf.metadata
        lines: list[str] = []
        for page in pdf.pages:
            for line in page.extract_text_lines(return_chars=True):
                chars = line.get("chars") or []
                text = line["text"].strip()
                if not text:
                    continue
                bold = chars and all("Bold" in c.get("fontname", "") for c in chars if c["text"].strip())
                if bold:
                    size = max(c.get("size", 0) for c in chars)
                    lines += ["", ("# " if size >= 14 else "## ") + text, ""]
                else:
                    lines.append(text)
    keys = {"DocId": "doc_id", "Title": "title", "DocType": "doc_type", "LastVerified": "last_verified",
            "CitationStatus": "citation_status", "Author": "owner"}
    meta = {ours: str(info[theirs]) for theirs, ours in keys.items() if theirs in info}
    return meta, "\n".join(lines).strip() + "\n"


def load_document(path: Path, root: Path) -> Document:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        meta, body = _pdf(path)
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        body = data.pop("text")
        meta = {k: str(v) for k, v in data.items()}
    else:
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_KEYS if not meta.get(k)]
    if missing:
        raise ValueError(f"{path.name}: front matter is missing {missing}")
    extra = {k: v for k, v in meta.items() if k not in REQUIRED_KEYS}
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.name
    return Document(doc_id=meta["doc_id"], title=meta["title"], doc_type=meta["doc_type"],
                    last_verified=date.fromisoformat(meta["last_verified"]), citation_status=meta["citation_status"],
                    text=body, path=relative, meta=extra)


def load_documents(root: Optional[Path] = None) -> list[Document]:
    """Every supported document under `root` (default knowledge/raw), sorted by path."""
    root = Path(root) if root else config.get_settings().knowledge_dir / "raw"
    paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED
                   and not p.name.startswith("."))
    return [load_document(p, root) for p in paths]

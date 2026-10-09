"""Knowledge-base source documents (Req. Section 9.5; Architecture Spec Section 8)."""

from __future__ import annotations

import json
import re
from datetime import date

import pdfplumber
import pytest

from src.config import REPO_ROOT

RAW = REPO_ROOT / "knowledge" / "raw"
FRONT_MATTER_KEYS = {"doc_id", "title", "doc_type", "last_verified", "citation_status"}
FOLDER_DOC_TYPE = {"runbooks": "runbook", "postmortems": "postmortem", "service_docs": "service_doc",
                   "regulatory": "regulatory"}

EXPECTED = {
    "runbooks": ["RB-PAY-003", "RB-KFK-001", "RB-DB-002", "RB-NET-004", "RB-PAY-005", "RB-GEN-001", "RB-GEN-002",
                 "RB-GEN-003"],
    "postmortems": ["PM-2025-011", "PM-2025-014", "PM-2025-019", "PM-2025-023", "PM-2025-027", "PM-2025-031"],
    "service_docs": ["SVC-000"] + [f"SVC-{i:03d}" for i in range(1, 12)],
    "regulatory": ["REG-001", "REG-002"],
}
PDF_DOCS = {"PM-2025-019", "REG-001"}


def front_matter(path):
    """Front matter of a Markdown document, or the Info dictionary of a PDF, as the loader will read it."""
    if path.suffix == ".pdf":
        with pdfplumber.open(path) as pdf:
            info = pdf.metadata
        return {"doc_id": info["DocId"], "title": info["Title"], "doc_type": info["DocType"],
                "last_verified": info["LastVerified"], "citation_status": info["CitationStatus"]}
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path.name
    header = text[4:].split("\n---\n", 1)[0]
    return {k.strip(): v.strip().strip('"') for k, _, v in (line.partition(":") for line in header.splitlines())}


def documents():
    return sorted(p for p in RAW.rglob("*") if p.is_file() and p.parent.name != "verified")


def test_expected_documents_present():
    found = {folder: sorted(p.name.split("_")[0] for p in (RAW / folder).glob("*.*")) for folder in EXPECTED}
    assert found == {folder: sorted(ids) for folder, ids in EXPECTED.items()}
    assert {p.name.split("_")[0] for p in RAW.rglob("*.pdf")} == PDF_DOCS


@pytest.mark.parametrize("path", documents(), ids=lambda p: p.name)
def test_front_matter(path):
    meta = front_matter(path)
    assert FRONT_MATTER_KEYS <= set(meta)
    assert path.name.startswith(meta["doc_id"] + "_")
    assert meta["doc_type"] == FOLDER_DOC_TYPE[path.parent.name]
    assert meta["citation_status"] == "verified"
    assert date.fromisoformat(meta["last_verified"]) <= date(2026, 10, 9)


@pytest.mark.parametrize("path", sorted((RAW / "runbooks").glob("*.md")), ids=lambda p: p.name)
def test_runbooks_have_numbered_sections(path):
    """Citations take the form 'RB-PAY-003 §2', so sections are numbered from 1."""
    numbers = [int(n) for n in re.findall(r"^## (\d+)\. ", path.read_text(encoding="utf-8"), flags=re.M)]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) >= 4


@pytest.mark.parametrize("path", sorted(RAW.rglob("*.pdf")), ids=lambda p: p.name)
def test_pdfs_are_readable_with_headings_in_bold(path):
    with pdfplumber.open(path) as pdf:
        text = "\n".join(page.extract_text() for page in pdf.pages)
        fonts = {c["fontname"] for page in pdf.pages for c in page.chars}
    assert "Helvetica-Bold" in fonts and "1. " in text
    source = REPO_ROOT / "data" / "generators" / "pdf_sources" / (path.stem + ".md")
    last_heading = re.findall(r"^## (.+)$", source.read_text(encoding="utf-8"), flags=re.M)[-1]
    assert last_heading in text  # nothing lost at a page break


def test_one_document_is_deliberately_stale():
    """RB-GEN-002 is older than STALE_DOC_DAYS (180) so the stale-citation flag can be demonstrated."""
    stale = [p.name for p in documents()
             if (date(2026, 10, 9) - date.fromisoformat(front_matter(p)["last_verified"])).days > 180]
    assert stale == ["RB-GEN-002_traffic_throttling_load_shedding.md"]


def test_golden_doc_ids_exist():
    doc_ids = {front_matter(p)["doc_id"] for p in documents()}
    rubric = json.loads((REPO_ROOT / "data" / "eval_rubric.json").read_text(encoding="utf-8"))
    for case in rubric["cases"]:
        assert set(case["expected_doc_ids"]) <= doc_ids, case["case_id"]


def test_documents_cite_only_existing_documents():
    doc_ids = {front_matter(p)["doc_id"] for p in documents()}
    for path in documents():
        if path.suffix == ".md":
            cited = set(re.findall(r"\b(?:RB-[A-Z]{2,4}-\d{3}|PM-\d{4}-\d{3}|REG-\d{3}|SVC-\d{3})\b",
                                   path.read_text(encoding="utf-8")))
            assert cited <= doc_ids, (path.name, cited - doc_ids)


def test_retrieval_aliases_file_shape():
    """Empty at first; only approved adaptations add aliases (query word -> extra search terms)."""
    aliases = json.loads((REPO_ROOT / "knowledge" / "retrieval_aliases.json").read_text(encoding="utf-8"))
    assert isinstance(aliases["aliases"], dict)
    assert all(isinstance(k, str) and isinstance(v, list) and all(isinstance(t, str) for t in v)
               for k, v in aliases["aliases"].items())

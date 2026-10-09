"""Minimal text-only PDF writer for the two PDF knowledge-base documents
(Req. Section 9.5). No PDF library is pinned (pdfplumber only reads), so this
writes PDF 1.4 directly: Helvetica text, A4 pages, no timestamps, so the output
is byte-identical on every run.

Front matter goes into the PDF Info dictionary (DocId, DocType, LastVerified,
CitationStatus, Title), where the document loader reads it. Headings use the
bold font, so the loader can recover section boundaries from font names.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

PAGE_W, PAGE_H = 595, 842  # A4 in points
MARGIN = 56
STYLES = {"title": ("F2", 16, 26), "h2": ("F2", 12, 20), "body": ("F1", 10, 14), "blank": ("F1", 10, 8)}
WRAP = 92
_REPLACE = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
            "→": "->", "≤": "<=", "≥": ">=", "×": "x"}


def parse_markdown(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Split front matter (simple `key: value` lines) from the body; map body lines to styles."""
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        header, body = text[4:].split("\n---\n", 1)
        for line in header.splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
    lines: list[tuple[str, str]] = []
    for raw in body.splitlines():
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", raw.rstrip()).replace("`", "")
        if line.startswith("# "):
            lines.append(("title", line[2:]))
        elif line.startswith("## "):
            lines.append(("blank", ""))
            lines.append(("h2", line[3:]))
        elif not line.strip():
            lines.append(("blank", ""))
        else:
            indent = "   " if line.startswith("- ") else ""
            for i, part in enumerate(textwrap.wrap(line, WRAP - len(indent)) or [""]):
                lines.append(("body", (indent if i else "") + part if indent else part))
    return meta, lines


def _pdf_string(value: str) -> bytes:
    for old, new in _REPLACE.items():
        value = value.replace(old, new)
    raw = value.encode("cp1252", errors="replace")
    return b"(" + raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _pages(lines: list[tuple[str, str]]) -> list[bytes]:
    pages, ops, y = [], [], PAGE_H - MARGIN
    for style, text in lines:
        font, size, leading = STYLES[style]
        if y - leading < MARGIN:
            pages.append(b"\n".join(ops))
            ops, y = [], PAGE_H - MARGIN
            if style == "blank":
                continue
        y -= leading
        if text:
            ops.append(b"BT /%s %d Tf %d %d Td %s Tj ET" % (font.encode(), size, MARGIN, y, _pdf_string(text)))
    pages.append(b"\n".join(ops))
    return pages


def write_pdf(path: Path, meta: dict[str, str], lines: list[tuple[str, str]]) -> None:
    pages = _pages(lines)
    objects: list[bytes] = []
    n_pages = len(pages)
    page_ids = [5 + 2 * i for i in range(n_pages)]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [%s] /Count %d >>"
                   % (b" ".join(b"%d 0 R" % p for p in page_ids), n_pages))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    for i, content in enumerate(pages):
        content_id = page_ids[i] + 1
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
                       b"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>"
                       % (PAGE_W, PAGE_H, content_id))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content) + 1, content))
    info_keys = {"Title": "title", "DocId": "doc_id", "DocType": "doc_type", "LastVerified": "last_verified",
                 "CitationStatus": "citation_status", "Author": "owner"}
    info = b" ".join(b"/%s %s" % (k.encode(), _pdf_string(meta[v])) for k, v in info_keys.items() if v in meta)
    objects.append(b"<< %s >>" % info)
    info_id = len(objects)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, info_id, xref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


def markdown_to_pdf(source: Path, target: Path) -> None:
    meta, lines = parse_markdown(source.read_text(encoding="utf-8"))
    write_pdf(target, meta, lines)

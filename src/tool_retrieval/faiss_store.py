"""FAISS index over normalized vectors (cosine similarity via inner product),
with chunk metadata in index_meta.json (Architecture Spec Section 8).

The index is written through serialize/deserialize, so paths with spaces or
non-ASCII characters work on every platform.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import faiss
import numpy as np

from src import config

INDEX_FILE = "index.faiss"
META_FILE = "index_meta.json"


def _dir(index_dir: Optional[Path]) -> Path:
    return Path(index_dir) if index_dir else config.get_settings().faiss_index_dir


def _write(index_dir: Path, index: faiss.Index, meta: dict[str, Any]) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / INDEX_FILE).write_bytes(faiss.serialize_index(index).tobytes())
    tmp = index_dir / (META_FILE + ".tmp")
    tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    tmp.replace(index_dir / META_FILE)


def build_index(chunks: list[dict], vectors: np.ndarray, *, model: str, doc_hashes: dict[str, str],
                index_dir: Optional[Path] = None) -> dict[str, Any]:
    """Replace the index with these chunks and vectors (one row per chunk)."""
    if len(chunks) != len(vectors):
        raise ValueError("one vector per chunk is required")
    dim = int(vectors.shape[1])
    index = faiss.IndexFlatIP(dim)
    data = np.ascontiguousarray(vectors, dtype=np.float32)
    faiss.normalize_L2(data)
    index.add(data)
    meta = {"model": model, "dim": dim, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chunks": chunks, "doc_hashes": doc_hashes}
    _write(_dir(index_dir), index, meta)
    return meta


def load_index(index_dir: Optional[Path] = None) -> tuple[faiss.Index, dict[str, Any]]:
    directory = _dir(index_dir)
    if not (directory / INDEX_FILE).exists():
        raise FileNotFoundError(f"no FAISS index in {directory}; run python scripts/build_index.py")
    raw = np.frombuffer((directory / INDEX_FILE).read_bytes(), dtype=np.uint8)
    index = faiss.deserialize_index(raw)
    meta = json.loads((directory / META_FILE).read_text(encoding="utf-8"))
    return index, meta


def add_chunks(chunks: list[dict], vectors: np.ndarray, *, doc_hashes: dict[str, str],
               index_dir: Optional[Path] = None) -> dict[str, Any]:
    """Append chunks to the existing index (used for verified resolutions)."""
    directory = _dir(index_dir)
    index, meta = load_index(directory)
    if vectors.shape[1] != meta["dim"]:
        raise ValueError(f"vector dimension {vectors.shape[1]} does not match the index ({meta['dim']})")
    data = np.ascontiguousarray(vectors, dtype=np.float32)
    faiss.normalize_L2(data)
    index.add(data)
    meta["chunks"].extend(chunks)
    meta["doc_hashes"].update(doc_hashes)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write(directory, index, meta)
    return meta


def index_mtime(index_dir: Optional[Path] = None) -> float:
    path = _dir(index_dir) / META_FILE
    return path.stat().st_mtime if path.exists() else 0.0

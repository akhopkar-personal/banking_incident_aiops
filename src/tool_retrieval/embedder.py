"""Embeddings (Architecture Spec Section 8).

Backend `openai`: OpenAI embeddings through the configured base URL, batched,
cached by content hash in knowledge/processed/embedding_cache.json.
Backend `hash`: a deterministic bag-of-words hashing embedding for tests and
LLM-free development; it needs no API key and its retrieval quality is poor.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Sequence

import numpy as np

from src import config
from src.errors import RecoverableError
from src.schemas.enums import ErrorType

HASH_DIM = 512
_cache_lock = threading.Lock()


def model_name() -> str:
    s = config.get_settings()
    return f"hash-{HASH_DIM}" if s.embedding_backend == "hash" else s.embedding_model


def _hash_vector(text: str) -> np.ndarray:
    vec = np.zeros(HASH_DIM, dtype=np.float32)
    words = re.findall(r"[a-z0-9]+", text.lower())
    for token in words + [f"{a}_{b}" for a, b in zip(words, words[1:])]:
        digest = hashlib.sha1(token.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % HASH_DIM
        vec[index] += 1.0 if digest[4] % 2 else -1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _cache_path() -> Path:
    return config.get_settings().processed_dir / "embedding_cache.json"


def _load_cache() -> dict[str, list[float]]:
    path = _cache_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _save_cache(cache: dict[str, list[float]]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache), encoding="utf-8")
    tmp.replace(path)


def _openai_embed(texts: list[str]) -> list[list[float]]:
    from langchain_openai import OpenAIEmbeddings

    s = config.get_settings()
    client = OpenAIEmbeddings(model=s.embedding_model, base_url=s.openai_base_url, api_key=s.require_openai_key(),
                              max_retries=2)
    out: list[list[float]] = []
    try:
        for start in range(0, len(texts), s.embedding_batch_size):
            out.extend(client.embed_documents(texts[start:start + s.embedding_batch_size]))
    except Exception as exc:  # noqa: BLE001 - surfaced as a retryable tool failure
        raise RecoverableError(f"embedding call failed: {type(exc).__name__}", ErrorType.LLM_CALL_FAILED) from exc
    return out


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """L2-normalized float32 vectors, one row per text."""
    if not texts:
        return np.zeros((0, HASH_DIM), dtype=np.float32)
    if config.get_settings().embedding_backend == "hash":
        return np.vstack([_hash_vector(t) for t in texts])
    model = model_name()
    keys = [hashlib.sha256(f"{model}\n{t}".encode("utf-8")).hexdigest() for t in texts]
    with _cache_lock:
        cache = _load_cache()
        missing = [i for i, k in enumerate(keys) if k not in cache]
        if missing:
            vectors = _openai_embed([texts[i] for i in missing])
            for i, vector in zip(missing, vectors):
                cache[keys[i]] = vector
            _save_cache(cache)
        matrix = np.array([cache[k] for k in keys], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def embed_query(text: str) -> np.ndarray:
    return embed_texts([text])[0]

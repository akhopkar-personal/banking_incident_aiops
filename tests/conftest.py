"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config import Settings, get_settings


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 3, 14, 10, 5, tzinfo=timezone.utc)


_ENV_NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
              "LLM_MODEL", "JUDGE_MODEL", "LLM_ENABLED", "LOG_LEVEL", "TOOL_TRANSPORT", "EMBEDDING_BACKEND",
              "OUTBOX_DIR", "INCIDENT_STATE_DIR", "LOGS_DIR", "FAISS_INDEX_DIR", "PROCESSED_DIR",
              "VERIFIED_RESOLUTIONS_DIR", "DATA_DIR", "KNOWLEDGE_DIR")


def sandbox_settings(root, **overrides) -> Settings:
    """Settings that read the committed reference data and write only under `root`, with offline
    hash embeddings and in-process tools unless overridden."""
    values = dict(logs_dir=root / "logs", outbox_dir=root / "outbox", incident_state_dir=root / "state",
                  faiss_index_dir=root / "index", processed_dir=root / "processed",
                  verified_resolutions_dir=root / "verified", embedding_backend="hash", tool_transport="inprocess")
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(scope="session")
def hash_index(tmp_path_factory):
    """A FAISS index of the real knowledge base with hash embeddings, built once per test session."""
    from src.logger_setup import configure_logging
    from src.tool_retrieval import reindex_job

    root = tmp_path_factory.mktemp("kb")
    settings = sandbox_settings(root)
    with pytest.MonkeyPatch.context() as mp:
        for name in _ENV_NAMES:
            mp.delenv(name, raising=False)
        mp.setattr("src.config.get_settings", lambda: settings)
        configure_logging(settings.logs_dir)
        reindex_job.rebuild(index_dir=settings.faiss_index_dir)
    return settings.faiss_index_dir


@pytest.fixture
def sandbox(monkeypatch, tmp_path, hash_index):
    """Isolated settings for Phase 3 tests: writes go to tmp_path; the hash index is shared."""
    from src.logger_setup import configure_logging
    from src.tool_retrieval import retriever
    from src.tools import tool_registry

    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    settings = sandbox_settings(tmp_path, faiss_index_dir=hash_index)
    get_settings.cache_clear()
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    configure_logging(settings.logs_dir)
    monkeypatch.setattr(tool_registry, "_registry", None)
    retriever.clear_cache()
    yield settings
    if tool_registry._registry is not None:
        tool_registry._registry.close()
    get_settings.cache_clear()


@pytest.fixture
def isolated_settings(monkeypatch, tmp_path):
    """Settings that ignore the developer's .env and write under tmp_path."""
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "LANGFUSE_HOST", "LLM_MODEL", "JUDGE_MODEL", "LLM_ENABLED", "LOG_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None, logs_dir=tmp_path / "logs", data_dir=tmp_path / "data")
    get_settings.cache_clear()
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()

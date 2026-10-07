"""Shared test fixtures."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.config import Settings, get_settings


@pytest.fixture
def t0() -> datetime:
    return datetime(2026, 3, 14, 10, 5, tzinfo=timezone.utc)


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

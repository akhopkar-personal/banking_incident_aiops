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
              "VERIFIED_RESOLUTIONS_DIR", "DATA_DIR", "KNOWLEDGE_DIR", "FEEDBACK_DIR", "GOLDEN_DIR",
              "PROMPT_VERSIONS_PATH", "RETRIEVAL_ALIASES_PATH", "EVALUATION_DIR", "EVIDENCE_DIR")


def sandbox_settings(root, **overrides) -> Settings:
    """Settings that read the committed reference data and write only under `root`, with offline
    hash embeddings and in-process tools unless overridden. The files the learning loop writes
    (golden dataset, prompt versions, retrieval aliases) are copied under `root` first."""
    import shutil

    from src.config import REPO_ROOT

    golden = root / "golden"
    golden.mkdir(parents=True, exist_ok=True)
    for name in ("eval_rubric.json", "test_inputs.json", "prompt_versions.json"):
        shutil.copy(REPO_ROOT / "data" / name, golden / name)
    shutil.copy(REPO_ROOT / "knowledge" / "retrieval_aliases.json", golden / "retrieval_aliases.json")
    values = dict(logs_dir=root / "logs", outbox_dir=root / "outbox", incident_state_dir=root / "state",
                  faiss_index_dir=root / "index", processed_dir=root / "processed",
                  verified_resolutions_dir=root / "verified", feedback_dir=root / "feedback",
                  golden_dir=golden, prompt_versions_path=golden / "prompt_versions.json",
                  retrieval_aliases_path=golden / "retrieval_aliases.json", evaluation_dir=root / "evaluation",
                  evidence_dir=root / "evidence",
                  embedding_backend="hash", tool_transport="inprocess", retry_backoff_s=0,
                  rate_limit_backoff_s=0)
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


@pytest.fixture
def fake(sandbox):
    """Install a scripted model for a scenario: fake(scenario_id, **oracle_options)."""
    from src.agent import llm
    from tests.fake_llm import FakeChatModel, ScenarioOracle

    def install(scenario_id: str, **options) -> FakeChatModel:
        model_options = {k: options.pop(k) for k in ("fail_with", "delay_s", "finish_reason") if k in options}
        model = FakeChatModel(ScenarioOracle(scenario_id, **options), **model_options)
        llm.set_chat_model_factory(lambda: model)
        return model

    yield install
    llm.set_chat_model_factory(None)

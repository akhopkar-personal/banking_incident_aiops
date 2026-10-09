"""Configuration (Architecture Spec Section 15)."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from src.config import REPO_ROOT, Settings


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("OPENAI_", "LANGFUSE_", "LLM_", "JUDGE_", "REWARD_", "STRUCTURED_")):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults(clean_env):
    s = Settings(_env_file=None)
    assert s.llm_model == "gpt-4o-mini"
    assert s.judge_model == "gpt-4o-mini"          # defaults to llm_model
    assert s.openai_base_url == "https://api.openai.com/v1"
    assert s.llm_max_output_tokens == 1500
    assert s.structured_output_method == "function_calling"
    assert s.max_node_retries == 2
    assert s.confidence_gate_threshold == 0.5
    assert "payments-service" in s.customer_facing_services
    assert s.logs_dir == (REPO_ROOT / "logs").resolve()
    assert s.openai_api_key is None and not s.langfuse_configured()


def test_environment_overrides(clean_env):
    clean_env.setenv("OPENAI_BASE_URL", "https://openai.vocareum.com/v1/")
    clean_env.setenv("LLM_MODEL", "gpt-4o")
    clean_env.setenv("JUDGE_MODEL", "gpt-4.1")
    clean_env.setenv("LLM_ENABLED", "false")
    clean_env.setenv("OPENAI_API_KEY", "voc-test-key-123456789")
    s = Settings(_env_file=None)
    assert s.openai_base_url == "https://openai.vocareum.com/v1"   # trailing slash removed
    assert s.llm_model == "gpt-4o" and s.judge_model == "gpt-4.1"
    assert s.llm_enabled is False
    assert s.require_openai_key() == "voc-test-key-123456789"
    assert "voc-test-key-123456789" not in repr(s)                 # secrets are hidden


def test_empty_judge_model_means_llm_model(clean_env):
    clean_env.setenv("JUDGE_MODEL", "")
    assert Settings(_env_file=None).judge_model == "gpt-4o-mini"


def test_structured_output_method_cannot_be_json_schema(clean_env):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, structured_output_method="json_schema")


def test_reward_weights_validated(clean_env):
    with pytest.raises(ValidationError, match="sum to 1.0"):
        Settings(_env_file=None, reward_weights={"mean_rating": 0.5, "win_rate": 0.5,
                                                 "approval_rate": 0.5, "fix_outcome_rate": 0.5})
    with pytest.raises(ValidationError, match="exactly the keys"):
        Settings(_env_file=None, reward_weights={"mean_rating": 1.0})


def test_missing_openai_key_gives_clear_error(clean_env):
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        Settings(_env_file=None).require_openai_key()


def test_scenario_dir(clean_env, tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.scenario_dir("SC-02") == tmp_path / "telemetry" / "sc02_kafka_consumer_lag"
    assert s.scenario_dir("FX-ALERT-STORM").name == "alert_storm_fixture"
    with pytest.raises(ValueError, match="unknown scenario_id"):
        s.scenario_dir("SC-99")


def test_apply_to_environment(clean_env):
    clean_env.setenv("OPENAI_BASE_URL", "https://openai.vocareum.com/v1")
    s = Settings(_env_file=None)
    clean_env.delenv("OPENAI_BASE_URL")
    s.apply_to_environment()
    assert os.environ["OPENAI_BASE_URL"] == "https://openai.vocareum.com/v1"
    assert os.environ["LANGFUSE_BASE_URL"] == s.langfuse_host


def test_secret_values(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "voc-abcdefghijklmnop")
    clean_env.setenv("LANGFUSE_SECRET_KEY", "sk-lf-0000-1111")
    assert set(Settings(_env_file=None).secret_values()) == {"voc-abcdefghijklmnop", "sk-lf-0000-1111"}


def test_phase3_settings_and_derived_paths(clean_env):
    s = Settings(_env_file=None)
    assert s.tool_transport == "mcp" and s.tool_call_timeout_s == 10 and s.embedding_backend == "openai"
    assert s.outbox_dir == s.data_dir / "outbox" and s.incident_state_dir == s.data_dir / "incident_state"
    assert s.faiss_index_dir == s.knowledge_dir / "faiss_index"
    assert s.verified_resolutions_dir == s.knowledge_dir / "raw" / "postmortems" / "verified"
    clean_env.setenv("TOOL_TRANSPORT", "inprocess")
    clean_env.setenv("OUTBOX_DIR", "")
    s = Settings(_env_file=None)
    assert s.tool_transport == "inprocess" and s.outbox_dir == s.data_dir / "outbox"
    env = s.path_environment()
    assert env["OUTBOX_DIR"] == str(s.outbox_dir) and "OPENAI_API_KEY" not in env
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tool_transport="http")

"""Configuration (Architecture Spec Section 15).

This is the only module that reads environment variables. Settings come from
the process environment and the repository's .env file; every field can be
overridden by an environment variable of the same name in upper case.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]

# scenario_id -> directory under data/telemetry/ (Req. Section 9.4, 17).
SCENARIO_DIRS: dict[str, str] = {
    "SC-01": "sc01_bad_release",
    "SC-02": "sc02_kafka_consumer_lag",
    "SC-03": "sc03_db_saturation",
    "SC-04": "sc04_tls_expiry",
    "SC-05": "sc05_duplicate_debits",
    "FX-FAILURE": "failure_fixture",
    "FX-LLM-DOWN": "llm_down_fixture",
    "FX-ALERT-STORM": "alert_storm_fixture",
    "FX-INJECTION": "injection_fixture",
}

DEFAULT_CUSTOMER_FACING_SERVICES = [
    "mobile-app", "net-banking", "api-gateway", "auth-service", "payments-service", "accounts-service",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- .env variables (Section 15.1)
    openai_api_key: Optional[SecretStr] = None
    openai_base_url: str = "https://api.openai.com/v1"
    langfuse_public_key: Optional[SecretStr] = None
    langfuse_secret_key: Optional[SecretStr] = None
    langfuse_host: str = "https://cloud.langfuse.com"
    llm_model: str = "gpt-4o-mini"
    judge_model: str = ""  # empty means the same as llm_model
    embedding_model: str = "text-embedding-3-small"
    llm_enabled: bool = True
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    tool_transport: Literal["mcp", "inprocess"] = "mcp"

    # --- Tools and retrieval (Sections 7, 8)
    tool_call_timeout_s: float = Field(default=10, gt=0)
    # The first MCP call starts the server process, which imports the tool code.
    mcp_startup_timeout_s: float = Field(default=60, gt=0)
    # "hash" is a deterministic offline embedding for tests and LLM-free development only;
    # retrieval quality needs "openai".
    embedding_backend: Literal["openai", "hash"] = "openai"
    embedding_batch_size: int = Field(default=64, gt=0)
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)

    # --- LLM call rules (Section 6.1)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_timeout_s: float = Field(default=30, gt=0)
    llm_max_output_tokens: int = Field(default=1500, gt=0)
    structured_output_method: Literal["function_calling"] = "function_calling"

    # --- Reliability and cost (Req. 13.2, 13.6)
    max_node_retries: int = Field(default=2, ge=0)
    # Exponential backoff between retries: 1 s, 2 s (Architecture Spec Section 4.4). Tests set 0.
    retry_backoff_s: float = Field(default=1.0, ge=0)
    cost_cap_usd_per_run: float = Field(default=0.15, gt=0)
    token_cap_per_run: int = Field(default=60_000, gt=0)
    # USD per 1,000 tokens. gpt-4o-mini and text-embedding-3-small list prices;
    # confirm against the provider's current price list.
    price_per_1k_input: float = 0.00015
    price_per_1k_output: float = 0.0006
    price_per_1k_embedding: float = 0.00002

    # --- Agents, gates and dispatch
    rca_tool_budget: int = Field(default=8, gt=0)
    change_lookback_min: int = Field(default=180, gt=0)
    confidence_gate_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    insufficient_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    page_rate_limit_min: int = Field(default=15, gt=0)
    storm_incident_threshold: int = Field(default=3, gt=0)
    stale_doc_days: int = Field(default=180, gt=0)
    customer_facing_services: list[str] = Field(default_factory=lambda: list(DEFAULT_CUSTOMER_FACING_SERVICES))

    # --- Learning loop (Req. 12.5, 12.6)
    adaptation_min_occurrences: int = Field(default=2, ge=1)
    max_reviewer_share: float = Field(default=0.30, gt=0.0, le=1.0)
    reward_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "mean_rating": 0.35, "win_rate": 0.30, "approval_rate": 0.15, "fix_outcome_rate": 0.20,
        }
    )
    reward_min_signals: int = Field(default=10, ge=1)
    pairwise_held_out_share: float = Field(default=0.5, gt=0.0, lt=1.0)
    win_rate_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    length_bias_max_pct: float = Field(default=20, ge=0)
    regression_tolerance_points: float = Field(default=5, ge=0)

    # --- Paths (relative paths are resolved against the repository root)
    data_dir: Path = Path("data")
    knowledge_dir: Path = Path("knowledge")
    logs_dir: Path = Path("logs")
    # Writable stores. Empty means the default under data_dir / knowledge_dir; tests point
    # them at a temporary directory while still reading the committed reference data.
    outbox_dir: Optional[Path] = None
    incident_state_dir: Optional[Path] = None
    faiss_index_dir: Optional[Path] = None
    processed_dir: Optional[Path] = None
    verified_resolutions_dir: Optional[Path] = None

    @field_validator("data_dir", "knowledge_dir", "logs_dir", "outbox_dir", "incident_state_dir",
                     "faiss_index_dir", "processed_dir", "verified_resolutions_dir", mode="before")
    @classmethod
    def _resolve_against_repo(cls, value: object) -> Optional[Path]:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        path = Path(value)  # type: ignore[arg-type]
        return path if path.is_absolute() else (REPO_ROOT / path).resolve()

    @field_validator("openai_base_url", "langfuse_host")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _derived_and_checked(self) -> "Settings":
        if not self.judge_model:
            self.judge_model = self.llm_model
        expected = {"mean_rating", "win_rate", "approval_rate", "fix_outcome_rate"}
        if set(self.reward_weights) != expected:
            raise ValueError(f"reward_weights must have exactly the keys {sorted(expected)}")
        if abs(sum(self.reward_weights.values()) - 1.0) > 1e-6:
            raise ValueError("reward_weights must sum to 1.0")
        self.outbox_dir = self.outbox_dir or self.data_dir / "outbox"
        self.incident_state_dir = self.incident_state_dir or self.data_dir / "incident_state"
        self.faiss_index_dir = self.faiss_index_dir or self.knowledge_dir / "faiss_index"
        self.processed_dir = self.processed_dir or self.knowledge_dir / "processed"
        self.verified_resolutions_dir = (self.verified_resolutions_dir
                                         or self.knowledge_dir / "raw" / "postmortems" / "verified")
        return self

    # --- helpers

    def secret_values(self) -> list[str]:
        """Configured secret values, used by redaction to scrub them from any text."""
        secrets = (self.openai_api_key, self.langfuse_public_key, self.langfuse_secret_key)
        return [s.get_secret_value() for s in secrets if s is not None and s.get_secret_value()]

    def require_openai_key(self) -> str:
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
            raise RuntimeError("OPENAI_API_KEY is not set; add it to .env (see .env.example)")
        return self.openai_api_key.get_secret_value()

    def langfuse_configured(self) -> bool:
        return self.langfuse_public_key is not None and self.langfuse_secret_key is not None

    def scenario_dir(self, scenario_id: str) -> Path:
        """Telemetry directory for a scenario or fixture ID, such as 'SC-01'."""
        try:
            return self.data_dir / "telemetry" / SCENARIO_DIRS[scenario_id]
        except KeyError:
            raise ValueError(f"unknown scenario_id {scenario_id!r}; known: {sorted(SCENARIO_DIRS)}") from None

    def reference_file(self, name: str) -> Path:
        """A reference data file under data_dir, such as 'severity_rules.yaml'."""
        return self.data_dir / name

    def path_environment(self) -> dict[str, str]:
        """Settings a child process (the MCP server) needs to read and write the same places."""
        names = ("data_dir", "knowledge_dir", "logs_dir", "outbox_dir", "incident_state_dir", "faiss_index_dir",
                 "processed_dir", "verified_resolutions_dir")
        env = {name.upper(): str(getattr(self, name)) for name in names}
        env["EMBEDDING_BACKEND"] = self.embedding_backend
        env["EMBEDDING_MODEL"] = self.embedding_model
        env["LOG_LEVEL"] = self.log_level
        return env

    def apply_to_environment(self) -> None:
        """Export settings that libraries read from the environment themselves.

        The OpenAI SDK (used inside DeepEval) reads OPENAI_BASE_URL, and LangFuse
        reads its host from LANGFUSE_HOST or LANGFUSE_BASE_URL. Our own code
        always passes these values explicitly.
        """
        os.environ["OPENAI_BASE_URL"] = self.openai_base_url
        os.environ["LANGFUSE_HOST"] = self.langfuse_host
        os.environ["LANGFUSE_BASE_URL"] = self.langfuse_host
        os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once."""
    return Settings()

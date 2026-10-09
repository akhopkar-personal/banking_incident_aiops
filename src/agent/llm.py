"""LLM access for the agents (Architecture Spec Sections 4.4, 4.5, 6.1).

- One chat model factory (`ChatOpenAI` through OPENAI_BASE_URL, `max_retries=0`,
  output capped at `llm_max_output_tokens`). Tests replace it with a fake.
- Structured output always uses `method="function_calling"`; a truncated
  (`finish_reason == "length"`) or unparsable answer is a RecoverableError
  (`schema_invalid`).
- `with_retries` retries RecoverableError up to MAX_NODE_RETRIES times with
  exponential backoff; the exception that finally escapes carries `attempts`.
- `RunBudget` counts tokens and estimated cost per run and raises
  CostCapExceeded (never retried) past the caps.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

from src import config
from src.errors import CostCapExceeded, RecoverableError
from src.logger_setup import log_error, log_interaction
from src.schemas.enums import ErrorType

M = TypeVar("M", bound=BaseModel)
T = TypeVar("T")


# ------------------------------------------------------------------ model

def _default_factory() -> Any:
    from langchain_openai import ChatOpenAI

    s = config.get_settings()
    return ChatOpenAI(model=s.llm_model, base_url=s.openai_base_url, api_key=s.require_openai_key(),
                      temperature=s.llm_temperature, timeout=s.llm_timeout_s, max_retries=0,
                      max_tokens=s.llm_max_output_tokens)


_factory: Callable[[], Any] = _default_factory


def set_chat_model_factory(factory: Optional[Callable[[], Any]]) -> None:
    """Replace the chat model (tests use a scripted fake). None restores ChatOpenAI."""
    global _factory
    _factory = factory or _default_factory


def chat_model() -> Any:
    try:
        return _factory()
    except RuntimeError as exc:  # for example OPENAI_API_KEY is not set
        raise RecoverableError(str(exc), ErrorType.LLM_UNAVAILABLE) from exc


# ----------------------------------------------------------------- budget

@dataclass
class RunBudget:
    run_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    by_agent: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def cost_usd(self) -> float:
        s = config.get_settings()
        return self.input_tokens / 1000 * s.price_per_1k_input + self.output_tokens / 1000 * s.price_per_1k_output

    def add(self, agent: str, usage: Optional[dict[str, Any]]) -> dict[str, int]:
        """Record one call's usage; raise CostCapExceeded past either cap. Returns the per-agent delta."""
        usage = usage or {}
        tokens_in, tokens_out = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        s = config.get_settings()
        with self._lock:
            self.input_tokens += tokens_in
            self.output_tokens += tokens_out
            self.by_agent[agent] = self.by_agent.get(agent, 0) + tokens_in + tokens_out
            total = self.input_tokens + self.output_tokens
            cost = self.cost_usd
        if total > s.token_cap_per_run or cost > s.cost_cap_usd_per_run:
            raise CostCapExceeded(f"run {self.run_id} used {total} tokens (${cost:.4f}); caps are "
                                  f"{s.token_cap_per_run} tokens and ${s.cost_cap_usd_per_run}")
        return {agent: tokens_in + tokens_out}


_budgets: dict[str, RunBudget] = {}
_budget_lock = threading.Lock()


def budget(run_id: str) -> RunBudget:
    with _budget_lock:
        return _budgets.setdefault(run_id, RunBudget(run_id))


def release_budget(run_id: str) -> Optional[RunBudget]:
    with _budget_lock:
        return _budgets.pop(run_id, None)


# ---------------------------------------------------------------- retries

def with_retries(fn: Callable[[], T], what: str, component: str, incident_id: str, run_id: str) -> T:
    """Call fn; retry RecoverableError up to MAX_NODE_RETRIES times (backoff 1 s, 2 s; 5 s, 10 s plus jitter
    after a rate-limit error)."""
    s = config.get_settings()
    attempts = 0
    while True:
        attempts += 1
        try:
            return fn()
        except RecoverableError as exc:
            exc.attempts = attempts  # type: ignore[attr-defined]
            if attempts > s.max_node_retries:
                raise
            log_error("node_retry", component=component, what=what, attempt=attempts, error_type=exc.error_type.value,
                      error=str(exc), incident_id=incident_id, run_id=run_id)
            delay = s.retry_backoff_s * (2 ** (attempts - 1))
            if "RateLimit" in str(exc):  # the gateway throttles parallel evaluation runs: wait longer, de-synced
                delay = max(delay, s.rate_limit_backoff_s * (2 ** (attempts - 1)) * random.uniform(1.0, 1.5))
            time.sleep(delay)


def _classify(exc: Exception) -> RecoverableError:
    name = type(exc).__name__
    unavailable = ("APIConnectionError", "APITimeoutError", "AuthenticationError", "PermissionDeniedError",
                   "ConnectError", "ReadTimeout", "Timeout")
    kind = ErrorType.LLM_UNAVAILABLE if any(n in name for n in unavailable) else ErrorType.LLM_CALL_FAILED
    return RecoverableError(f"LLM call failed: {name}", kind)


# --------------------------------------------------------------- the calls

@dataclass
class LlmCall:
    """Who is calling, for logging, budgeting and tracing."""

    agent: str
    incident_id: str
    run_id: str
    llm_available: bool = True
    # The calling node's LangGraph config: passing it on nests the LLM call under the node's
    # trace span (LangFuse callback handler).
    parent_config: Optional[dict[str, Any]] = None

    def _config(self) -> dict[str, Any]:
        return {**(self.parent_config or {}), "run_name": f"{self.agent}_llm"}

    def _check_available(self) -> None:
        if not self.llm_available:
            raise RecoverableError("the LLM is unavailable for this run", ErrorType.LLM_UNAVAILABLE)

    def _record(self, message: Any, started: float, kind: str) -> dict[str, int]:
        usage = getattr(message, "usage_metadata", None) or {}
        meta = getattr(message, "response_metadata", None) or {}
        log_interaction("llm_call", component=self.agent, kind=kind, model=meta.get("model_name"),
                        input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                        finish_reason=meta.get("finish_reason"), latency_ms=round((time.perf_counter() - started) * 1000),
                        incident_id=self.incident_id, run_id=self.run_id)
        return budget(self.run_id).add(self.agent, usage)

    def structured(self, schema: type[M], messages: list[Any]) -> tuple[M, dict[str, int]]:
        """One structured call. Returns (parsed result, token usage delta for the agent)."""
        self._check_available()
        started = time.perf_counter()
        runnable = chat_model().with_structured_output(schema, method="function_calling", include_raw=True)
        try:
            out = runnable.invoke(messages, config=self._config())
        except RecoverableError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider errors become retryable failures
            raise _classify(exc) from exc
        raw = out.get("raw")
        usage = self._record(raw, started, "structured")
        finish = (getattr(raw, "response_metadata", None) or {}).get("finish_reason")
        if finish == "length":
            raise RecoverableError(f"{schema.__name__} output was cut off at the token limit", ErrorType.SCHEMA_INVALID)
        parsed = out.get("parsed")
        if out.get("parsing_error") is not None or not isinstance(parsed, schema):
            detail = str(out.get("parsing_error") or "no structured output").splitlines()
            raise RecoverableError(f"{schema.__name__} output did not validate: {' '.join(detail[:3])[:300]}",
                                   ErrorType.SCHEMA_INVALID)
        return parsed, usage

    def with_tools(self, tools: list[Any], messages: list[Any]) -> tuple[Any, dict[str, int]]:
        """One call with tools bound; returns (AI message, usage delta)."""
        self._check_available()
        started = time.perf_counter()
        try:
            message = chat_model().bind_tools(tools).invoke(messages, config=self._config())
        except RecoverableError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        return message, self._record(message, started, "tools")

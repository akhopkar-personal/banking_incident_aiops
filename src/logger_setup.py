"""Structured JSON logging to three files (Architecture Spec Section 13;
Req. Section 16).

Always log through `log_interaction`, `log_error` or `log_eval`, never through
the `logging` module directly, so that every line carries the common fields
and every string is redacted.
"""

from __future__ import annotations

import contextvars
import logging
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator, Optional

from pythonjsonlogger.json import JsonFormatter

from src.safety.redaction import redact_value, register_secret_provider

# Event names from Req. Section 16. Unknown names are rejected.
ALLOWED_EVENTS: frozenset[str] = frozenset({
    # Carried from v0.7
    "tool_call", "llm_call", "guardrail_block", "review_decision", "eval_result",
    "feedback_candidate_created", "feedback_candidate_promoted", "adaptation_proposed",
    "adaptation_applied", "adaptation_reverted", "episodic_memory_written",
    "episodic_memory_promoted", "node_retry", "system_error",
    # v0.8
    "adaptation_approved", "adaptation_rejected", "rules_change", "evidence_report_generated",
    "anomaly_detected", "incident_deduplicated", "severity_rules_evaluated", "severity_disagreement",
    "fast_path_page", "confidence_gate_flagged", "dispatch_page", "dispatch_itsm", "notification_sent",
    "dispatch_suppressed", "reclassified", "action_policy_flag", "tool_access_blocked",
    "kill_switch_active", "resolution_recorded", "root_cause_verified", "resolution_indexed",
    "rating_recorded", "pairwise_label_recorded", "reward_computed", "dpo_exported",
    # v0.10
    "node_completed", "feedback_candidate_discarded", "mcp_server_started", "mcp_server_error",
})

# Events that belong to one investigation run: they must carry incident_id and run_id.
RUN_SCOPED_EVENTS: frozenset[str] = frozenset({
    "tool_call", "llm_call", "guardrail_block", "review_decision", "feedback_candidate_created",
    "episodic_memory_written", "node_retry", "node_completed", "system_error",
    "incident_deduplicated", "severity_rules_evaluated", "severity_disagreement", "fast_path_page",
    "confidence_gate_flagged", "dispatch_page", "dispatch_itsm", "notification_sent",
    "dispatch_suppressed", "reclassified", "action_policy_flag", "tool_access_blocked",
    "kill_switch_active", "resolution_recorded", "root_cause_verified", "resolution_indexed",
    "rating_recorded",
})

# Fields taken from the logging context when the call does not supply them.
CONTEXT_FIELDS = (
    "incident_id", "run_id", "trace_id", "langfuse_trace_id",
    "feedback_id", "adaptation_id", "prompt_version_set",
)

LOG_FILES = {"interactions": "interactions.log", "error": "error.log", "eval": "eval.log"}
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("aiops_log_context", default={})
_loggers: dict[str, logging.Logger] = {}


class _AiopsJsonFormatter(JsonFormatter):
    """Writes our fields as the top-level keys of each JSON line."""

    def process_log_record(self, log_data: dict[str, Any]) -> dict[str, Any]:
        payload = log_data.pop("aiops", None) or {}
        log_data.pop("message", None)  # the event name is already in payload
        log_data.update(payload)
        return log_data


def configure_logging(logs_dir: Optional[Path] = None, level: Optional[str] = None) -> Path:
    """Set up the three rotating JSON log files. Safe to call more than once;
    a later call (for example from tests) reconfigures the destination."""
    from src.config import get_settings

    settings = get_settings()
    logs_dir = Path(logs_dir) if logs_dir is not None else settings.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    level_name = (level or settings.log_level).upper()
    register_secret_provider(settings.secret_values)

    for key, filename in LOG_FILES.items():
        logger = logging.getLogger(f"aiops.{key}")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
        handler = RotatingFileHandler(
            logs_dir / filename, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
        )
        handler.setFormatter(_AiopsJsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(level_name)
        logger.propagate = False
        _loggers[key] = logger
    return logs_dir


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Add fields (incident_id, run_id, trace_id, ...) to every log line written
    inside the block, including from LangGraph worker threads."""
    token = _context.set({**_context.get(), **{k: v for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


def _build(event: str, component: str, level: str, fields: dict[str, Any]) -> dict[str, Any]:
    if event not in ALLOWED_EVENTS:
        raise ValueError(f"unknown log event {event!r}; add it to Req. Section 16 and ALLOWED_EVENTS")
    if not component:
        raise ValueError("component is required")
    ctx = _context.get()
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "level": level,
        "event": event,
        "component": component,
    }
    for name in CONTEXT_FIELDS:
        value = fields.pop(name, None)
        if value is None:
            value = ctx.get(name)
        if value is not None:
            payload[name] = value
    if event in RUN_SCOPED_EVENTS:
        missing = [name for name in ("incident_id", "run_id") if name not in payload]
        if missing:
            raise ValueError(f"event {event!r} is run-scoped and needs {missing}")
    payload.update(fields)
    return redact_value(payload)


def _emit(key: str, level: int, payload: dict[str, Any]) -> None:
    logger = _loggers.get(key)
    if logger is None:
        configure_logging()
        logger = _loggers[key]
    logger.log(level, payload["event"], extra={"aiops": payload})


def log_interaction(event: str, *, component: str, level: str = "INFO", **fields: Any) -> None:
    """Agent and service activity: tool and LLM calls, gates, dispatch, human decisions."""
    payload = _build(event, component, level, fields)
    _emit("interactions", logging.getLevelName(level), payload)


def log_eval(event: str, *, component: str, **fields: Any) -> None:
    """Evaluation results, reward scores, pairwise results, adaptation metrics."""
    payload = _build(event, component, "INFO", fields)
    _emit("eval", logging.INFO, payload)


def log_error(event: str, *, component: str, exc: Optional[BaseException] = None, **fields: Any) -> None:
    """Failures. With `exc`, the exception type, message and stack trace are
    included (redacted like every other field)."""
    if exc is not None:
        fields.setdefault("error_type_name", type(exc).__name__)
        fields.setdefault("error_message", str(exc))
        fields.setdefault(
            "stack_trace", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
    payload = _build(event, component, "ERROR", fields)
    _emit("error", logging.ERROR, payload)

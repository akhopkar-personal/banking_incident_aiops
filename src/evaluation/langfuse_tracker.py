"""LangFuse tracing (Architecture Spec Sections 11.2, 13; Req. FR-26).

One trace per investigation run: a root observation with the run ID as
session and the scenario and run mode as tags. The LangChain callback handler
passed to the graph turns every node into a span and every LLM call into a
generation. Every function here is best-effort: if LangFuse is not configured
or fails, the run continues untraced and the failure goes to error.log.

Score functions for evaluation and human feedback are added in Phase 6.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from src import config
from src.logger_setup import log_error


@dataclass
class RunTrace:
    trace_id: Optional[str] = None
    callbacks: list[Any] = field(default_factory=list)
    _span: Any = None

    def finish(self, output: dict[str, Any]) -> None:
        if self._span is None:
            return
        try:
            self._span.update(output=output)
        except Exception as exc:  # noqa: BLE001
            log_error("system_error", component="langfuse_tracker", stage="trace_update", error=str(exc),
                      incident_id="unknown", run_id="unknown")


@contextmanager
def investigation_trace(run_id: str, scenario_id: Optional[str], run_mode: str) -> Iterator[RunTrace]:
    """Yields a RunTrace (empty when LangFuse is off). Exceptions from the body propagate unchanged."""
    settings = config.get_settings()
    if not settings.langfuse_configured():
        yield RunTrace()
        return
    stack = []
    trace = RunTrace()
    try:
        settings.apply_to_environment()
        import os

        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key.get_secret_value())
        os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key.get_secret_value())
        from langfuse import get_client, propagate_attributes
        from langfuse.langchain import CallbackHandler

        client = get_client()
        span_cm = client.start_as_current_observation(name="investigation", as_type="chain",
                                                      input={"scenario_id": scenario_id, "run_mode": run_mode})
        trace._span = span_cm.__enter__()
        stack.append(span_cm)
        attrs_cm = propagate_attributes(session_id=run_id, tags=[t for t in (scenario_id, run_mode) if t],
                                        trace_name="investigation", metadata={"run_id": run_id})
        attrs_cm.__enter__()
        stack.append(attrs_cm)
        trace.trace_id = client.get_current_trace_id()
        trace.callbacks = [CallbackHandler()]
    except Exception as exc:  # noqa: BLE001 - tracing must never stop a run
        log_error("system_error", component="langfuse_tracker", stage="trace_start", error=f"{type(exc).__name__}",
                  incident_id="unknown", run_id=run_id)
        while stack:
            try:
                stack.pop().__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        yield RunTrace()
        return
    try:
        yield trace
    finally:
        while stack:
            try:
                stack.pop().__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        try:
            from langfuse import get_client

            get_client().flush()
        except Exception as exc:  # noqa: BLE001
            log_error("system_error", component="langfuse_tracker", stage="flush", error=f"{type(exc).__name__}",
                      incident_id="unknown", run_id=run_id)

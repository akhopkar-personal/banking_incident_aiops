"""LangFuse tracing (Architecture Spec Sections 11.2, 13; Req. FR-26).

One trace per investigation run: a root observation with the run ID as
session and the scenario and run mode as tags. The LangChain callback handler
passed to the graph turns every node into a span and every LLM call into a
generation. Every function here is best-effort: if LangFuse is not configured
or fails, the run continues untraced and the failure goes to error.log.

Scores (Req. Section 12.4): evaluation metrics, human ratings and decisions,
pairwise choices and reward scores are attached to the run's trace. The golden
dataset is mirrored to a LangFuse dataset. All of these are best-effort too.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

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


# ------------------------------------------------------------------ scores and datasets


def _client() -> Any:
    """The LangFuse client, or None when LangFuse is not configured."""
    settings = config.get_settings()
    if not settings.langfuse_configured():
        return None
    import os

    settings.apply_to_environment()
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key.get_secret_value())
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key.get_secret_value())
    from langfuse import get_client

    return get_client()


def _best_effort(stage: str, body: Callable[[Any], Any], default: Any = None) -> Any:
    try:
        client = _client()
        if client is None:
            return default
        result = body(client)
        client.flush()
        return result
    except Exception as exc:  # noqa: BLE001 - LangFuse must never break evaluation or review
        log_error("system_error", component="langfuse_tracker", stage=stage, error=f"{type(exc).__name__}",
                  incident_id="unknown", run_id="unknown")
        return default


def attach_scores(trace_id: Optional[str], scores: dict[str, Any], comment: Optional[str] = None) -> int:
    """Numeric or categorical scores on one trace; None values are skipped. Returns the count sent."""
    values = {k: v for k, v in scores.items() if v is not None}
    if not trace_id or not values:
        return 0

    def body(client: Any) -> int:
        for name, value in values.items():
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            client.create_score(name=name, value=float(value) if numeric else str(value), trace_id=trace_id,
                                data_type="NUMERIC" if numeric else "CATEGORICAL", comment=comment)
        return len(values)

    return _best_effort("score", body, 0)


def attach_human_scores(trace_id: Optional[str], ratings: Optional[dict[str, int]], decision: str) -> int:
    scores: dict[str, Any] = {"reviewer_decision": decision}
    scores.update({f"rating_{k}": v for k, v in (ratings or {}).items()})
    return attach_scores(trace_id, scores)


def attach_pairwise(trace_ids: list[Optional[str]], choice: str, pair_id: str) -> int:
    return sum(attach_scores(t, {"pairwise_preference": choice}, comment=pair_id) for t in trace_ids if t)


def attach_reward(trace_id: Optional[str], version_set_key: str, score: Optional[float], signal_count: int) -> int:
    return attach_scores(trace_id, {"reward_score": score}, comment=f"{version_set_key}; {signal_count} signals")


def trace_url(trace_id: Optional[str]) -> Optional[str]:
    if not trace_id:
        return None
    return _best_effort("trace_url", lambda client: client.get_trace_url(trace_id=trace_id))


def sync_golden_dataset(cases: list[dict[str, Any]], version: str) -> int:
    """Mirror the golden cases to the LangFuse dataset (items are keyed by case ID, so re-syncing updates)."""
    name = config.get_settings().langfuse_dataset_name

    def body(client: Any) -> int:
        try:
            client.create_dataset(name=name, description="EventHub AIOps golden dataset (Req. Section 12.1)",
                                  metadata={"version": version})
        except Exception:  # noqa: BLE001 - it already exists
            pass
        for c in cases:
            expected = {k: v for k, v in c.items() if k.startswith("expected_") or k == "must_cite_evidence_ids"}
            client.create_dataset_item(dataset_name=name, id=c["case_id"], input=c.get("input"),
                                       expected_output=expected,
                                       metadata={"scenario_id": c["scenario_id"], "variant": c["variant"],
                                                 "version_added": c.get("version_added"),
                                                 "golden_version": version})
        return len(cases)

    return _best_effort("dataset_sync", body, 0)

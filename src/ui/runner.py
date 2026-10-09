"""Background investigation runs for the Investigate page.

Streamlit stops and restarts the page script whenever the user touches a
widget. Running the graph inside the script would abandon a half-dispatched
run, so each investigation runs in its own thread and the page polls its
progress. A rerun picks the job up again from the session's job ID.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from src.logger_setup import log_error

from .components import NODE_LABELS, describe_update

_MAX_JOBS = 20
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class Job:
    job_id: str
    started: float
    events: list[dict[str, Any]] = field(default_factory=list)  # node, label, detail, elapsed_s
    early: dict[str, str] = field(default_factory=dict)  # incident, rules severity, issue class, fast-path page
    result: Any = None  # InvestigationOutput or IntakeRejection
    error: Optional[str] = None
    finished: Optional[float] = None
    done: threading.Event = field(default_factory=threading.Event)

    @property
    def elapsed_s(self) -> float:
        return (self.finished or time.perf_counter()) - self.started


def _record(job: Job, node: str, update: dict[str, Any]) -> None:
    detail = describe_update(node, update)
    job.events.append({"node": node, "label": NODE_LABELS.get(node, node), "detail": detail,
                       "elapsed_s": round(time.perf_counter() - job.started, 1)})
    if node == "correlate_dedup" and update.get("incident") is not None:
        job.early["Incident"] = detail
    elif node == "severity_rules_pass1" and detail:
        job.early["Rules severity"] = detail
    elif node == "triage_agent" and update.get("triage") is not None:
        job.early["Issue class"] = update["triage"].issue_class.value
    elif node == "dispatch_fast_page":
        dispatch = update.get("dispatch")
        if dispatch is not None and dispatch.fast_path:
            job.early["Fast-path page"] = f"simulated page sent at {dispatch.page_time:%H:%M:%S} UTC " \
                                          f"({job.events[-1]['elapsed_s']} s after submit)"
        elif dispatch is not None and dispatch.suppressed:
            job.early["Fast-path page"] = "decided, but suppressed (evaluation mode)"


def start(**kwargs: Any) -> Job:
    """Start `run_investigation(**kwargs)` in a thread and return its job."""
    from src.agent.core_agent import run_investigation

    job = Job(job_id=uuid.uuid4().hex, started=time.perf_counter())

    def target() -> None:
        try:
            job.result = run_investigation(progress_callback=lambda node, update: _record(job, node, update),
                                           **kwargs)
        except Exception as exc:  # noqa: BLE001 - shown on the page; details go to error.log
            log_error("system_error", component="ui.investigate", exc=exc, incident_id="n/a", run_id="n/a")
            job.error = "The investigation failed unexpectedly. Details are in logs/error.log."
        finally:
            job.finished = time.perf_counter()
            job.done.set()

    with _lock:
        if len(_jobs) >= _MAX_JOBS:
            for old in [j for j in _jobs.values() if j.done.is_set()][: len(_jobs) - _MAX_JOBS + 1]:
                _jobs.pop(old.job_id, None)
        _jobs[job.job_id] = job
    threading.Thread(target=target, name=f"investigation-{job.job_id[:8]}", daemon=True).start()
    return job


def get(job_id: Optional[str]) -> Optional[Job]:
    return _jobs.get(job_id) if job_id else None


def outcome(job: Job) -> Union[str, None]:
    """'output', 'rejection', 'error' or None while running."""
    if not job.done.is_set():
        return None
    if job.error:
        return "error"
    from src.agent.core_agent import IntakeRejection

    return "rejection" if isinstance(job.result, IntakeRejection) else "output"

"""Append-only outbox shared by the dispatch mocks (Architecture Spec Section 7.4).

Every call appends one JSON line with `outbox_id`, `incident_id`, `at`,
`simulated: true` and the payload, after a PII re-check of every text field.
Nothing here makes a network call.
"""

from __future__ import annotations

import contextvars
import json
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from src import config
from src.safety.redaction import redact_record

FILES = {"pages": "pages.jsonl", "itsm": "itsm.jsonl", "notifications": "notifications.jsonl"}
# Streamlit sessions are threads of one process, so a process-wide lock serializes writers.
_lock = threading.Lock()


_scope: contextvars.ContextVar[Optional[Path]] = contextvars.ContextVar("aiops_outbox_scope", default=None)


def outbox_dir() -> Path:
    return _scope.get() or config.get_settings().outbox_dir


@contextmanager
def scoped(directory: Path) -> Iterator[Path]:
    """Write to `directory` in this context: the evaluation harness measures live dispatch in a sandbox."""
    token = _scope.set(directory)
    try:
        yield directory
    finally:
        _scope.reset(token)


def new_outbox_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def append(kind: str, incident_id: Optional[str], payload: dict[str, Any], *, prefix: str,
           keep_plain: tuple[str, ...] = ()) -> dict[str, Any]:
    """Write one outbox line and return it. Fields in `keep_plain` (routing data such as an
    email recipient from the stakeholder directory) are not redacted."""
    plain = {k: payload[k] for k in keep_plain if k in payload}
    redacted, _ = redact_record({k: v for k, v in payload.items() if k not in plain})
    line = {"outbox_id": new_outbox_id(prefix), "incident_id": incident_id,
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "simulated": True,
            **redacted, **plain}
    path = outbox_dir() / FILES[kind]
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def read(kind: str) -> list[dict[str, Any]]:
    path = outbox_dir() / FILES[kind]
    if not path.exists():
        return []
    with _lock:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reset_outbox() -> Optional[Path]:
    """Move the outbox files to archive/<timestamp>/ ("reset demo"). Never deletes them."""
    directory = outbox_dir()
    with _lock:
        present = [directory / name for name in FILES.values() if (directory / name).exists()]
        if not present:
            return None
        target = directory / "archive" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target.mkdir(parents=True, exist_ok=True)
        for path in present:
            shutil.move(str(path), str(target / path.name))
        return target

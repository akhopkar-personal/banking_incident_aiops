"""lookup_stakeholders (Architecture Spec Section 7.3; Req. FR-52): exact match
on service and severity group in data/stakeholders.json. Used only by the
Notification Service, never exposed to agents or over MCP."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from src import config


@lru_cache(maxsize=4)
def _load(path: str, _mtime: float) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def lookup_stakeholders(service: str, severity_group: str) -> list[dict]:
    path = config.get_settings().reference_file("stakeholders.json")
    rows = _load(str(path), path.stat().st_mtime)
    return [{"name": r["name"], "role": r["role"], "email": r["email"], "chat_handle": r["chat_handle"]}
            for r in rows if r["service"] == service and r["severity_group"] == severity_group]

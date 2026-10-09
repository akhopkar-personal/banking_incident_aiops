"""Simulated pager (Architecture Spec Section 7.4). Writes data/outbox/pages.jsonl."""

from __future__ import annotations

from typing import Any, Literal

from . import outbox


def page_oncall(incident_id: str, team: str, summary: str, mode: Literal["new", "update"] = "new") -> dict[str, Any]:
    """Page `team`. `update` attaches new information to a page already sent; it is not a new page."""
    if mode not in ("new", "update"):
        raise ValueError("mode must be 'new' or 'update'")
    return outbox.append("pages", incident_id, {"team": team, "mode": mode, "summary": f"[SIMULATED] {summary}"},
                         prefix="PAGE")

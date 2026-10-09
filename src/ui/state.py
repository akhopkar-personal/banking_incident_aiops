"""UI session state (Architecture Spec Section 14; Req. Section 15).

Only UI state lives here: the selected persona and reviewer ID, page
selections and the latest run. Durable data is always read from the Incident
State Store and data/ files. After 30 minutes without activity the UI state is
cleared (the persona and reviewer ID are kept).
"""

from __future__ import annotations

import time
from typing import Any, Optional

import streamlit as st

from src import config
from src.schemas.enums import ReviewerRole

ROLE_LABELS = {
    ReviewerRole.ONCALL: "On-call engineer",
    ReviewerRole.ESCALATION: "Incident Escalation Team",
    ReviewerRole.PRODUCTION_SUPPORT: "Production Support",
    ReviewerRole.INCIDENT_COMMANDER: "Incident commander",
    ReviewerRole.SME: "SME",
    ReviewerRole.EVALUATION_ENGINEER: "Evaluation Engineer",
}

ROLE = "role"
REVIEWER_ID = "reviewer_id"
SELECTED_INCIDENT = "selected_incident"
SELECTED_RUN = "selected_run"
LAST_RUN_ID = "last_run_id"
LAST_REJECTION = "last_rejection"
LAST_ACTIVITY = "last_activity"
_KEPT = (ROLE, REVIEWER_ID, LAST_ACTIVITY)


def touch() -> bool:
    """Record activity. Returns True if the session had timed out and its UI state was cleared."""
    now = time.time()
    last = st.session_state.get(LAST_ACTIVITY)
    timed_out = last is not None and now - last > config.get_settings().ui_session_timeout_min * 60
    if timed_out:
        for key in [k for k in st.session_state.keys() if k not in _KEPT]:
            del st.session_state[key]
    st.session_state[LAST_ACTIVITY] = now
    return timed_out


def role() -> ReviewerRole:
    return ReviewerRole(st.session_state.get(ROLE, ReviewerRole.ONCALL.value))


def reviewer_id() -> str:
    return (st.session_state.get(REVIEWER_ID) or "reviewer-1").strip() or "reviewer-1"


def get(key: str, default: Any = None) -> Any:
    return st.session_state.get(key, default)


def put(key: str, value: Any) -> None:
    st.session_state[key] = value


def select_incident(incident_id: Optional[str], run_id: Optional[str] = None) -> None:
    st.session_state[SELECTED_INCIDENT] = incident_id
    st.session_state[SELECTED_RUN] = run_id

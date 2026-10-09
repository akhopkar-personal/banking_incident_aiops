"""Outbox page (Req. Section 15): every simulated page, ITSM ticket and
notification, with timestamps and audience, plus the "reset demo" action.
Nothing here was sent anywhere: the mocks only write data/outbox/*.jsonl.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.services import incident_state_store as store  # noqa: E402
from src.tools.dispatch_mocks import outbox  # noqa: E402
from src.ui import components, state  # noqa: E402

KINDS = {"pages": "Pages", "itsm": "ITSM tickets", "notifications": "Notifications"}
FIRST_COLUMNS = ["at", "incident_id", "outbox_id", "simulated"]


def table(lines: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(lines)
    first = [c for c in FIRST_COLUMNS if c in frame.columns]
    frame = frame[first + [c for c in frame.columns if c not in first]]
    return frame.sort_values("at", ascending=False)


def reset_demo() -> None:
    with st.expander("Reset demo"):
        st.write("Moves the outbox files to `data/outbox/archive/<time>/`. Nothing is deleted. Feedback, "
                 "ratings and the knowledge base are kept.")
        also_incidents = st.checkbox("Also archive the incident records (incident numbering restarts; a re-run "
                                     "of a scenario then creates a new incident instead of updating the old one)",
                                     key="reset_incidents")
        sure = st.checkbox("I want to reset the demo", key="reset_sure")
        if st.button("Reset demo", disabled=not sure, key="reset_button"):
            archived = outbox.reset_outbox()
            message = f"Outbox archived to {archived}." if archived else "The outbox was already empty."
            if also_incidents:
                moved = store.archive_state()
                message += f" Incident records archived to {moved}." if moved else " No incident records."
                state.select_incident(None)
            state.put("outbox_flash", message)
            st.rerun()


components.setup_page("Outbox", "📤")
st.title("Simulated outbox")
st.markdown("Every line below is **SIMULATED**: written by the mock pager, ITSM, chat and email adapters to "
            "the local outbox. No real person was paged or notified.")
if state.get("outbox_flash"):
    st.success(state.get("outbox_flash"))
    state.put("outbox_flash", None)

data = {kind: outbox.read(kind) for kind in KINDS}
incidents = sorted({line.get("incident_id") for lines in data.values() for line in lines if line.get("incident_id")},
                   reverse=True)
chosen = st.selectbox("Incident", ["All incidents"] + incidents, key="outbox_incident")
cols = st.columns(len(KINDS))
for col, (kind, label) in zip(cols, KINDS.items()):
    col.metric(f"{label} (simulated)", len(data[kind]))
for kind, label in KINDS.items():
    lines = [line for line in data[kind] if chosen == "All incidents" or line.get("incident_id") == chosen]
    st.subheader(f"{label} · simulated")
    if lines:
        st.dataframe(table(lines), hide_index=True, width="stretch")
    else:
        st.caption("None.")
reset_demo()

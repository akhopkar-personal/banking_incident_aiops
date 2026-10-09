"""Resolve and Verify page (Req. Section 15, FR-54, FR-55; H-6 and H-7).

The resolver records the actual root cause, the actions taken and the fix
outcome. The SME or the escalation lead then confirms, corrects or rejects
the root cause. Only a confirmed or corrected resolution is indexed into the
knowledge base, where later investigations can cite it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.feedback import resolution  # noqa: E402
from src.schemas.enums import FixOutcome, IncidentState, ReviewerRole  # noqa: E402
from src.schemas.feedback import ResolutionRecord, VerificationRecord  # noqa: E402
from src.services import incident_state_store as store  # noqa: E402
from src.ui import components, state  # noqa: E402

VERIFY_ROLES = (ReviewerRole.SME, ReviewerRole.ESCALATION)
FIX_LABELS = {FixOutcome.WORKED: "Worked", FixOutcome.PARTLY: "Partly worked",
              FixOutcome.DID_NOT_WORK: "Did not work", FixOutcome.NOT_TRIED: "Not tried"}
VERDICTS = {"confirmed": "Confirm", "corrected": "Correct", "rejected": "Reject"}
FLASH = "resolve_flash"


def errors_of(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
    return str(exc)


def resolution_form(view, output: dict, role: ReviewerRole) -> None:
    st.subheader("1. Resolution (H-6)")
    if view.resolution:
        r = view.resolution
        st.success(f"Recorded by {r.get('resolver_role')}: fix outcome **{r.get('fix_outcome')}**, "
                   f"{r.get('resolution_time_min')} min.")
        st.markdown(f"**Actual root cause:** {r.get('actual_root_cause')}")
        st.markdown(f"**Actions taken:** {r.get('actions_taken')}")
        return
    hypotheses = output.get("hypotheses") or []
    with st.form(f"resolve_{view.incident_id}"):
        cause = st.text_area("Actual root cause", value=hypotheses[0]["root_cause"] if hypotheses else "",
                             help="Starts from the top hypothesis; write what was really wrong.")
        actions = st.text_area("Actions taken")
        outcome = st.radio("Did the fix work?", list(FIX_LABELS), format_func=FIX_LABELS.get, horizontal=True)
        minutes = st.number_input("Time to resolve (minutes)", min_value=0.0, value=30.0, step=5.0)
        if st.form_submit_button("Record the resolution", type="primary"):
            try:
                record = ResolutionRecord(incident_id=view.incident_id, actual_root_cause=cause.strip(),
                                          actions_taken=actions.strip(), fix_outcome=outcome,
                                          resolution_time_min=minutes, resolver_role=role, at=store.now())
                resolution.record_resolution(record, state.reviewer_id())
                state.put(FLASH, "Resolution recorded. The incident is RESOLVED and waits for verification.")
                st.rerun()
            except (ValueError, ValidationError) as exc:
                st.error(errors_of(exc))


def verification_form(view, role: ReviewerRole) -> None:
    st.subheader("2. Root-cause verification (H-7)")
    if view.verification:
        v = view.verification
        text = f"{v.get('verdict')} by {v.get('verifier_role')}"
        if v.get("corrected_root_cause"):
            text += f": {v['corrected_root_cause']}"
        st.success(text)
        return
    if not view.resolution:
        st.caption("Record the resolution first.")
        return
    if role not in VERIFY_ROLES:
        st.caption("The SME or the escalation lead verifies the root cause (switch role in the sidebar).")
        return
    with st.form(f"verify_{view.incident_id}"):
        verdict = st.radio("Verdict", list(VERDICTS), format_func=VERDICTS.get, horizontal=True,
                           help="Confirm or Correct closes the incident as verified and indexes it. Reject closes "
                                "it unverified; nothing is indexed.")
        corrected = st.text_area("Corrected root cause (required for Correct)")
        if st.form_submit_button("Record the verification", type="primary"):
            try:
                record = VerificationRecord(incident_id=view.incident_id, verdict=verdict,
                                            corrected_root_cause=corrected.strip() or None,
                                            verifier_role=role, at=store.now())
                with st.spinner("Verifying and indexing…"):
                    result = resolution.record_verification(record, state.reviewer_id())
                message = f"Incident closed as {result['state'].value}."
                if result["indexed"]:
                    message += f" Indexed into the knowledge base as {result['doc_id']}."
                elif result["index_error"]:
                    message += " " + result["index_error"]
                state.put(FLASH, message)
                st.rerun()
            except (ValueError, ValidationError) as exc:
                st.error(errors_of(exc))


def indexing_status(view) -> None:
    st.subheader("3. Knowledge base")
    if view.indexed_doc_id:
        st.success(f"Indexed as **{view.indexed_doc_id}**: later investigations can retrieve and cite it.")
    elif view.state == IncidentState.CLOSED_VERIFIED:
        st.warning("Verified but not indexed yet.")
        if st.button("Retry indexing", key=f"reindex_{view.incident_id}"):
            try:
                doc_id = resolution.retry_indexing(view.incident_id)
                state.put(FLASH, f"Indexed as {doc_id}.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001 - shown to the user; details in error.log
                st.error(f"Indexing failed again: {type(exc).__name__}.")
    elif view.state == IncidentState.CLOSED_UNVERIFIED:
        st.info("Closed unverified: the resolution is not indexed (only verified resolutions can be cited).")
    else:
        st.caption("Indexed only after a confirmed or corrected verification.")


role = components.setup_page("Resolve and Verify", "✅")
st.title("Resolve and verify")
if state.get(FLASH):
    st.success(state.get(FLASH))
    state.put(FLASH, None)
views = sorted(store.list_incidents(), key=lambda v: v.created_at, reverse=True)
if not views:
    st.info("No incidents yet.")
    st.stop()
show_closed = st.toggle("Show closed incidents", key="show_closed")
closed = (IncidentState.CLOSED_VERIFIED, IncidentState.CLOSED_UNVERIFIED)
shown = [v for v in views if show_closed or v.state not in closed]
if not shown:
    st.info("Every incident is closed. Turn on 'Show closed incidents' to see them.")
    st.stop()
by_id = {v.incident_id: v for v in shown}
ids = list(by_id)
selected = state.get(state.SELECTED_INCIDENT)
incident_id = st.selectbox("Incident", ids, index=ids.index(selected) if selected in ids else 0,
                           format_func=lambda i: components.incident_label(by_id[i]), key="resolve_pick")
if incident_id != selected:
    state.select_incident(incident_id)
view = by_id[incident_id]
output = store.load_output(view.run_ids[-1]) or {}
st.caption(f"Created {components.fmt_time(view.created_at)} · state {view.state.value} · runs {len(view.run_ids)}")
resolution_form(view, output, role)
verification_form(view, role)
indexing_status(view)

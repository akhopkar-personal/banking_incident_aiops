"""Review Queue page (Req. Section 15, FR-28; H-8).

Edits and rejections at review become feedback candidates. The SME promotes a
candidate into the golden dataset with its ground truth, or discards it with a
reason. Promoted cases join every later golden-set run and the LangFuse dataset.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.evaluation import golden_dataset  # noqa: E402
from src.feedback import feedback_loop  # noqa: E402
from src.schemas.enums import IssueClass, ReviewerRole, SeverityLevel  # noqa: E402
from src.ui import components, state  # noqa: E402

FLASH = "queue_flash"


def ids(text: str) -> list[str]:
    return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]


def show_candidate(candidate) -> None:
    output = candidate.original_output
    hypotheses = output.get("hypotheses") or []
    st.markdown(f"**{candidate.candidate_id}** · incident {candidate.incident_id} · run {candidate.run_id} · "
                f"issue type `{candidate.issue_type.value if candidate.issue_type else 'none'}` · "
                f"created {components.fmt_time(candidate.created_at)}")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**What the system said**")
        st.markdown(f"Severity {components.severity_badge((output.get('severity') or {}).get('level'))} · "
                    f"class `{output.get('issue_class')}` · status {output.get('status')}", unsafe_allow_html=True)
        if hypotheses:
            st.write(f"Top hypothesis ({hypotheses[0]['confidence']:.2f}): {hypotheses[0]['root_cause']}")
        for action in (output.get("recommended_actions") or [])[:4]:
            st.caption(f"{action['step']}. {action['action']} ({action['runbook_citation']})")
    with c2:
        st.markdown("**What the reviewer said**")
        st.write(candidate.reviewer_reason)
        st.code(candidate.reviewer_correction, language="json")
        st.caption(f"Prompt versions: {candidate.prompt_version_set}")


def promote_form(candidate, role: ReviewerRole) -> None:
    truth = feedback_loop.default_ground_truth(candidate)
    with st.form(f"promote_{candidate.candidate_id}"):
        st.markdown("**Promote into the golden dataset** (ground truth, pre-filled from the scenario and the edit)")
        cause = st.text_area("Expected root cause", value=truth.get("expected_root_cause") or "")
        c1, c2, c3 = st.columns(3)
        levels = [lv.value for lv in SeverityLevel]
        classes = [ic.value for ic in IssueClass]
        severity = c1.selectbox("Expected severity", levels,
                                index=levels.index(truth.get("expected_severity") or "S2"))
        issue_class = c2.selectbox("Expected issue class", classes,
                                   index=classes.index(truth.get("expected_issue_class") or "other"))
        runbook = c3.text_input("Expected runbook ID", value=truth.get("expected_runbook_id") or "")
        docs = st.text_input("Other expected documents (comma separated)",
                             value=", ".join(d for d in truth.get("expected_doc_ids") or [] if d != runbook))
        cite = st.text_input("Evidence that must be cited (comma separated)",
                             value=", ".join(truth.get("must_cite_evidence_ids") or []))
        change = st.text_input("Causal change ID (empty if none)", value=truth.get("expected_causal_change_id") or "")
        c4, c5 = st.columns(2)
        abstain = c4.checkbox("The correct answer is to abstain", value=bool(truth.get("expected_abstention")))
        regulatory = c5.checkbox("Regulatory case", value=bool(truth.get("expected_regulatory_flag")))
        if st.form_submit_button("Promote", type="primary"):
            try:
                version = feedback_loop.promote(candidate.candidate_id, {
                    "expected_root_cause": cause, "expected_severity": severity, "expected_issue_class": issue_class,
                    "expected_runbook_id": runbook.strip(), "expected_doc_ids": ids(docs),
                    "must_cite_evidence_ids": ids(cite), "expected_causal_change_id": change.strip() or None,
                    "expected_abstention": abstain, "expected_regulatory_flag": regulatory}, role, state.reviewer_id())
                state.put(FLASH, f"{candidate.candidate_id} promoted. Golden dataset is now {version}.")
                st.rerun()
            except (ValueError, ValidationError) as exc:
                st.error(str(exc))


def discard_form(candidate, role: ReviewerRole) -> None:
    with st.form(f"discard_{candidate.candidate_id}"):
        reason = st.text_input("Reason for discarding (required)")
        if st.form_submit_button("Discard"):
            try:
                feedback_loop.discard(candidate.candidate_id, reason, role, state.reviewer_id())
                state.put(FLASH, f"{candidate.candidate_id} discarded.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


role = components.setup_page("Review Queue", "🗂️")
st.title("Review queue")
st.caption("Edited and rejected recommendations wait here for the SME. Promoted cases join the golden dataset "
           "used by every evaluation run.")
if state.get(FLASH):
    st.success(state.get(FLASH))
    state.put(FLASH, None)
rubric = golden_dataset.rubric()
st.markdown(f"Golden dataset **{rubric['version']}** · {len(rubric['cases'])} cases "
            f"({sum(c.get('variant') == 'feedback' for c in rubric['cases'])} from feedback)")
queue = feedback_loop.review_queue()
if not queue:
    st.info("No candidates are waiting. A reviewer's Edit or Reject on the Incident page creates one.")
else:
    by_id = {c.candidate_id: c for c in queue}
    chosen = st.selectbox("Pending candidate", list(by_id), key="queue_pick",
                          format_func=lambda i: f"{i} · {by_id[i].incident_id} · "
                                                f"{by_id[i].issue_type.value if by_id[i].issue_type else ''}")
    candidate = by_id[chosen]
    show_candidate(candidate)
    if components.role_note((ReviewerRole.SME,), "Promoting or discarding is done by the SME"):
        promote_form(candidate, role)
        discard_form(candidate, role)
done = [c for c in feedback_loop.candidates() if c.status != "pending"]
if done:
    st.subheader("Decided")
    st.dataframe(pd.DataFrame([{"candidate": c.candidate_id, "incident": c.incident_id, "status": c.status,
                                "golden version": f"golden-v{c.golden_dataset_version_added}"
                                if c.golden_dataset_version_added else "", "discard reason": c.discard_reason or "",
                                "decided": components.fmt_time(c.resolved_at)} for c in done]),
                 hide_index=True, width="stretch")

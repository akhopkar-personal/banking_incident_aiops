"""Incident page (Req. Section 15; Architecture Spec Section 14).

Status band, severity with the rules that fired, hypotheses with one-click
evidence, change findings, recommended actions with risk and policy flags,
timeline, stakeholder summary and the dispatch record. Human steps on this
page: acknowledge the page (H-1), the "Needs human RCA" hand-off (H-2), review
with ratings (H-3), re-classify as Major (H-4) and the two-step confirmation of
high-risk actions (H-5). A SYSTEM_ERROR run has its own layout and is never
shown as a recommendation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.feedback import review_store  # noqa: E402
from src.schemas.enums import IssueType, ReviewerRole, SeverityLevel  # noqa: E402
from src.schemas.feedback import Ratings, Reclassification, ReviewDecision  # noqa: E402
from src.services import incident_state_store as store  # noqa: E402
from src.ui import components, state  # noqa: E402

REVIEW_ROLES = (ReviewerRole.ESCALATION, ReviewerRole.PRODUCTION_SUPPORT, ReviewerRole.INCIDENT_COMMANDER)
DECISIONS = {"approve": "Approve", "edit": "Edit", "reject": "Reject"}
FLASH = "incident_flash"


def flash_and_rerun(message: str) -> None:
    state.put(FLASH, message)
    st.rerun()


def errors_of(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())
    return str(exc)


def pick_incident() -> tuple[Any, str] | None:
    views = sorted(store.list_incidents(), key=lambda v: v.created_at, reverse=True)
    if not views:
        st.info("No incidents yet. Start one on the Investigate page.")
        st.page_link("app.py", label="Investigate", icon="🔎")
        return None
    by_id = {v.incident_id: v for v in views}
    ids = list(by_id)
    selected = state.get(state.SELECTED_INCIDENT)
    c1, c2 = st.columns([3, 2])
    incident_id = c1.selectbox("Incident", ids, index=ids.index(selected) if selected in ids else 0,
                               format_func=lambda i: components.incident_label(by_id[i]), key="incident_pick")
    if incident_id != selected:
        state.select_incident(incident_id)
    view = by_id[incident_id]
    runs = [r for r in reversed(view.run_ids) if store.load_output(r) is not None]
    if not runs:
        st.warning("This incident has no finished run yet.")
        return None
    chosen = state.get(state.SELECTED_RUN)
    run_id = c2.selectbox("Investigation run (newest first)", runs,
                          index=runs.index(chosen) if chosen in runs else 0, key=f"run_pick_{incident_id}",
                          help="An incident is investigated again when a repeat alert arrives (deduplicated).")
    state.put(state.SELECTED_RUN, run_id)
    return view, run_id


def rules_table(rules_fired: list[dict[str, Any]]) -> None:
    if rules_fired:
        st.dataframe(pd.DataFrame(rules_fired), hide_index=True, width="stretch")
    else:
        st.caption("No rule fired.")


# ------------------------------------------------------------------ sections


def summary_tab(output: dict[str, Any]) -> None:
    severity = output.get("severity") or {}
    st.markdown(f"**Severity** {components.severity_badge(severity.get('level'))} (decided by the rules)",
                unsafe_allow_html=True)
    st.write(severity.get("rationale", ""))
    if severity.get("llm_proposed_level") and severity["llm_proposed_level"] != severity.get("level"):
        st.caption(f"The Triage Agent proposed {severity['llm_proposed_level']}; the rules decide.")
    if severity.get("flags"):
        st.markdown("Flags: " + ", ".join(f"`{f}`" for f in severity["flags"]))
    rules_table(severity.get("rules_fired") or [])
    st.markdown("**Stakeholder summary**")
    st.info(output.get("stakeholder_summary") or "")
    if output.get("regulatory_notes"):
        st.markdown("**Regulatory notes**")
        st.warning(output["regulatory_notes"])
    if output.get("insufficient_evidence_reason"):
        st.markdown("**Why there is no recommendation**")
        st.info(output["insufficient_evidence_reason"])
    services = output.get("affected_services") or []
    if services:
        st.markdown("**Affected services:** " + ", ".join(f"{s['service']} ({s['role']})" for s in services))
    meta = output.get("metadata") or {}
    with st.expander("Run details"):
        st.markdown(f"Model `{meta.get('model')}` · rules `{meta.get('rules_version')}` · action policy "
                    f"`{meta.get('action_policy_version')}` · run mode `{meta.get('run_mode')}`")
        st.markdown("Prompt versions: " + ", ".join(f"{k} `{v}`" for k, v in
                                                    (meta.get("prompt_version_set") or {}).items()))
        if meta.get("guardrail_flags"):
            st.markdown("Guardrail flags: " + ", ".join(f"`{f}`" for f in meta["guardrail_flags"]))
        latency = meta.get("latency_ms_per_stage") or {}
        if latency:
            st.dataframe(pd.DataFrame([{"stage": k, "ms": v} for k, v in latency.items()]), hide_index=True)
        st.markdown(f"Tokens {sum((meta.get('token_counts') or {}).values())} · cost "
                    f"${meta.get('cost_estimate_usd') or 0:.4f}"
                    + (f" · LangFuse trace `{meta['langfuse_trace_id']}`" if meta.get("langfuse_trace_id") else ""))


def hypotheses_tab(output: dict[str, Any]) -> None:
    evidence = output.get("evidence") or {}
    hypotheses = output.get("hypotheses") or []
    if not hypotheses:
        st.caption("No hypotheses.")
    for h in hypotheses:
        with st.expander(f"#{h['rank']} · confidence {h['confidence']:.2f} · {h['root_cause']}",
                         expanded=h["rank"] == 1):
            st.markdown("**Supporting evidence** (click an ID to see the record)")
            components.evidence_drawer(h.get("supporting_evidence") or [], evidence, key=f"h{h['rank']}")
            missing = h.get("contradicting_or_missing_evidence") or []
            if missing:
                st.markdown("**Contradicting or missing evidence**")
                for item in missing:
                    st.markdown(f"- {item}")


def changes_tab(output: dict[str, Any]) -> None:
    findings = output.get("change_findings") or []
    if not findings:
        st.caption("No change in the look-back window was linked to this incident.")
        return
    for f in findings:
        linked = f"linked to hypothesis #{f['linked_hypothesis_rank']}" if f.get("linked_hypothesis_rank") \
            else "not linked to a hypothesis"
        path = "on the dependency path" if f.get("on_dependency_path") else "off the dependency path"
        st.markdown(f"**{f['change_id']}** · {f['change_type']} on {f['service']} · "
                    f"{f['time_before_onset_min']:.0f} min before onset · {path} · {linked}")
        st.write(f["rationale"])
        components.evidence_drawer([f["change_id"]], output.get("evidence") or {}, key=f"c{f['change_id']}")


def actions_tab(output: dict[str, Any]) -> None:
    actions = output.get("recommended_actions") or []
    if not actions:
        st.caption("No recommended actions.")
    for action in actions:
        components.action_card(action)


def timeline_tab(output: dict[str, Any]) -> None:
    events = output.get("timeline") or []
    if not events:
        st.caption("No timeline.")
        return
    evidence = output.get("evidence") or {}
    for i, event in enumerate(sorted(events, key=lambda e: e["timestamp"])):
        c1, c2, c3 = st.columns([2, 6, 1.4])
        c1.markdown(f"`{components.fmt_time(event['timestamp'])[11:19]}` {event['service']}")
        c2.write(f"[{event['source']}] {event['description']}")
        item = evidence.get(event["evidence_id"])
        with c3.popover(event["evidence_id"], width="stretch", disabled=item is None):
            if item:
                st.write(item["summary"])
                st.json(item.get("record") or {}, expanded=False)


def dispatch_section(view: Any, output: dict[str, Any], role: ReviewerRole) -> None:
    dispatch = output.get("dispatch") or {}
    st.markdown("**Dispatch record** (all simulated)")
    if dispatch.get("paged"):
        components.simulated(f"Paged the Incident Escalation Team at {components.fmt_time(dispatch.get('page_time'))}"
                             + (" through the critical fast path (rules only, before any LLM call)."
                                if dispatch.get("fast_path") else "."))
    if dispatch.get("itsm_ticket_id"):
        components.simulated(f"ITSM ticket {dispatch['itsm_ticket_id']}"
                             + (f", assigned to {dispatch['assigned_queue']}" if dispatch.get("assigned_queue")
                                else ""))
    notes = dispatch.get("notifications") or []
    if notes:
        st.dataframe(pd.DataFrame([{"channel": n["channel"], "audience": n["audience"],
                                    "time": components.fmt_time(n["time"]), "outbox_id": n["outbox_id"],
                                    "simulated": True} for n in notes]), hide_index=True, width="stretch")
    if dispatch.get("suppressed"):
        st.caption("Evaluation run: nothing was dispatched. Intended dispatch:")
        components.json_block(dispatch.get("intended") or {})
    st.page_link("pages/2_Outbox.py", label="Open the outbox", icon="📤")
    acknowledge(view, role)
    reclassify(view, output, role)


def acknowledge(view: Any, role: ReviewerRole) -> None:
    """H-1: acknowledge the simulated page (time to acknowledge, an MTTA proxy)."""
    if not view.paged_at:
        return
    st.markdown("**Page acknowledgement (H-1)**")
    if view.acknowledged_at:
        seconds = (view.acknowledged_at - view.paged_at[0]).total_seconds()
        st.success(f"Acknowledged at {components.fmt_time(view.acknowledged_at)}, {seconds:.0f} s after the page.")
    elif role in review_store.ACKNOWLEDGE_ROLES:
        if st.button("Acknowledge the page", key=f"ack_{view.incident_id}"):
            try:
                result = review_store.record_acknowledgement(view.incident_id, role, state.reviewer_id())
                flash_and_rerun(f"Page acknowledged after {result['seconds_to_acknowledge']:.0f} s.")
            except ValueError as exc:
                st.error(errors_of(exc))
    else:
        st.caption("Not acknowledged yet. The escalation team, on-call engineer or incident commander acknowledges.")


def current_level(output: dict[str, Any]) -> SeverityLevel | None:
    level = (output.get("severity") or output.get("rules_severity") or {}).get("level")
    return SeverityLevel(level) if level else None


def reclassify(view: Any, output: dict[str, Any], role: ReviewerRole) -> None:
    """H-4 / FR-53: Production Support raises the incident to Major (or from S2 to S1); the final gate pages."""
    level = current_level(output)
    st.markdown("**Re-classification (H-4)**")
    for r in view.reclassifications:
        st.info(f"Re-classified {r['from_level']} → {r['to_level']} by {r['actor']} at "
                f"{components.fmt_time(r['at'])}: {r['reason']}")
    targets = [lv.value for lv in (SeverityLevel.S2, SeverityLevel.S1) if level and lv.is_more_severe_than(level)]
    if view.reclassifications or not targets:
        if not view.reclassifications:
            st.caption("Already S1: nothing to re-classify.")
        return
    if role not in review_store.RECLASSIFY_ROLES:
        st.caption("Production Support can re-classify this incident as Major (switch role in the sidebar).")
        return
    with st.form(f"reclassify_{view.incident_id}"):
        to_level = st.radio("Raise to", targets, horizontal=True)
        reason = st.text_area("Reason (required)")
        if st.form_submit_button("Re-classify and page"):
            try:
                record = Reclassification(incident_id=view.incident_id, from_level=level,
                                          to_level=SeverityLevel(to_level), reason=reason.strip(),
                                          reviewer_role=role, at=store.now())
                dispatch = review_store.record_reclassification(record, state.reviewer_id())
                flash_and_rerun(f"Re-classified as {to_level}. Simulated page sent; "
                                f"{len(dispatch.notifications)} simulated notifications.")
            except (ValueError, ValidationError) as exc:
                st.error(errors_of(exc))


def handoff(view: Any, run_id: str, output: dict[str, Any], role: ReviewerRole) -> None:
    """H-2: after investigating a "Needs human RCA" run, was the cause in the ranked list?"""
    if not output.get("needs_human_rca"):
        return
    st.markdown("**Needs human RCA: hand-off result (H-2)**")
    done = view.gate_handoffs.get(run_id)
    if done:
        found = f"yes, rank {done.get('rank_found')}" if done.get("found_in_ranked_list") else "no"
        st.success(f"Recorded: the real cause was in the ranked hypotheses: {found}.")
        return
    if role not in REVIEW_ROLES:
        st.caption("The receiving team records what the human investigation found.")
        return
    ranks = [h["rank"] for h in output.get("hypotheses") or []]
    with st.form(f"handoff_{run_id}"):
        found = st.radio("Was the real cause among the ranked hypotheses?", ["Yes", "No"], horizontal=True)
        rank = st.selectbox("At which rank?", ranks or [1])
        note = st.text_input("Note (optional)")
        if st.form_submit_button("Record the hand-off result"):
            try:
                review_store.record_gate_handoff(view.incident_id, run_id, found == "Yes", rank, role,
                                                 state.reviewer_id(), note.strip())
                flash_and_rerun("Hand-off result recorded.")
            except (ValueError, ValidationError) as exc:
                st.error(errors_of(exc))


def high_risk_confirmations(run_id: str, output: dict[str, Any]) -> list[int]:
    """H-5: two-step confirmation per high-risk action. Records the decision only; nothing runs."""
    steps = review_store.high_risk_steps(output)
    if not steps:
        return []
    st.markdown("**High-risk actions: two-step confirmation (H-5)**")
    st.caption("Confirming records your decision only. The system never executes an action.")
    actions = {a["step"]: a for a in output["recommended_actions"]}
    confirmed = []
    for step in steps:
        first = st.checkbox(f"Step {step}: I have read the risk of “{actions[step]['action']}”",
                            key=f"hr1_{run_id}_{step}")
        second = st.checkbox(f"Step {step}: I confirm this step is approved for a human to carry out",
                             key=f"hr2_{run_id}_{step}", disabled=not first)
        if first and second:
            confirmed.append(step)
    return confirmed


def review_section(view: Any, run_id: str, output: dict[str, Any], role: ReviewerRole) -> None:
    """H-3: Approve, Edit or Reject with ratings; reason and issue type required for Edit and Reject."""
    handoff(view, run_id, output, role)
    st.markdown("**Review (H-3)**")
    review = view.reviews.get(run_id)
    if review:
        ratings = review.get("ratings") or {}
        st.success(f"{review['status']} by {review['actor']} ({review.get('reviewer_id', '')}) at "
                   f"{components.fmt_time(review['at'])}")
        if review.get("reason"):
            st.write(f"Reason: {review['reason']} · issue type `{review.get('issue_type')}`")
        if ratings:
            st.write(" · ".join(f"{components.RATING_FIELDS[k]} {v}/5" for k, v in ratings.items()))
        if review.get("high_risk_confirmations"):
            st.write(f"High-risk steps confirmed: {review['high_risk_confirmations']}")
        if review.get("edited_output"):
            st.markdown("Edited fields:")
            components.json_block(review["edited_output"])
        if review.get("candidate_id"):
            st.caption(f"Feedback candidate {review['candidate_id']} is waiting in the SME review queue.")
        return
    if role not in REVIEW_ROLES:
        st.caption("The receiving team reviews: switch the role in the sidebar to the Incident Escalation Team, "
                   "Production Support or the incident commander.")
        return
    confirmed = high_risk_confirmations(run_id, output)
    hypotheses = output.get("hypotheses") or []
    actions = output.get("recommended_actions") or []
    level = current_level(output)
    originals = {
        "root_cause": hypotheses[0]["root_cause"] if hypotheses else "",
        "severity": level.value if level else "S4",
        "recommended_actions": "\n".join(f"{a['step']}. {a['action']}" for a in actions),
        "stakeholder_summary": output.get("stakeholder_summary") or "",
    }
    with st.form(f"review_{run_id}"):
        decision = st.radio("Decision", list(DECISIONS), format_func=DECISIONS.get, horizontal=True)
        st.markdown("Ratings (1 = poor, 5 = excellent)")
        ratings = components.rating_widget(f"rate_{run_id}")
        reason = st.text_area("Reason (required for Edit and Reject)")
        issue = st.selectbox("Issue type (required for Edit and Reject)", [None] + [i.value for i in IssueType],
                             format_func=lambda v: "—" if v is None else v.replace("_", " "))
        with st.expander("Edited output (used only when the decision is Edit)"):
            lower = [lv.value for lv in SeverityLevel if level is None or lv.rank >= level.rank]
            edited = {
                "root_cause": st.text_area("Root cause", value=originals["root_cause"]),
                "severity": st.selectbox("Severity (Edit can only lower it)", lower),
                "recommended_actions": st.text_area("Recommended actions", value=originals["recommended_actions"],
                                                    height=140),
                "stakeholder_summary": st.text_area("Stakeholder summary", value=originals["stakeholder_summary"]),
            }
        if st.form_submit_button("Submit review", type="primary"):
            changed = {k: v.strip() for k, v in edited.items() if v.strip() != originals[k].strip()}
            if decision == "edit" and not changed:
                st.error("Edit needs at least one changed field; use Approve if nothing needs changing.")
                return
            try:
                record = ReviewDecision(incident_id=view.incident_id, run_id=run_id, decision=decision,
                                        reason=reason.strip() or None, issue_type=issue, ratings=Ratings(**ratings),
                                        edited_output=changed if decision == "edit" else None,
                                        high_risk_confirmations=confirmed, reviewer_role=role, at=store.now())
                result = review_store.record_review(record, state.reviewer_id())
                extra = f" Feedback candidate {result['candidate_id']} created." if result["candidate_id"] else ""
                flash_and_rerun(f"Review recorded: {result['status'].value}.{extra}")
            except (ValueError, ValidationError) as exc:
                st.error(errors_of(exc))


def system_error_layout(view: Any, output: dict[str, Any], role: ReviewerRole) -> None:
    """Req. 8.1 and 15: no recommendation layout; say what failed and what was dispatched."""
    error = output.get("error_detail") or {}
    st.error(error.get("message", "The investigation could not complete."))
    st.caption(f"Failed step `{error.get('failed_node')}` · error type `{error.get('error_type')}` · "
               f"retries {error.get('retry_count')}")
    if (output.get("dispatch") or {}).get("paged"):
        st.info("The on-call team was paged based on monitoring rules.")
    rules = output.get("rules_severity") or {}
    if rules:
        st.markdown(f"**Rules severity** {components.severity_badge(rules.get('level'))} "
                    f"({rules.get('pass_name')}, rules `{rules.get('rules_version')}`)", unsafe_allow_html=True)
        rules_table(rules.get("rules_fired") or [])
    dispatch_section(view, output, role)


role = components.setup_page("Incident", "🧭")
st.title("Incident")
if state.get(FLASH):
    st.success(state.get(FLASH))
    state.put(FLASH, None)
picked = pick_incident()
if picked is not None:
    view, run_id = picked
    output = store.load_output(run_id)
    hypotheses = output.get("hypotheses") or []
    status = review_store.review_status(view.incident_id, run_id)
    components.status_band(status, needs_human_rca=bool(output.get("needs_human_rca")),
                           top_confidence=hypotheses[0]["confidence"] if hypotheses else None,
                           detail=f"Incident state {view.state.value} · root service {view.root_service or 'unknown'}")
    if view.resolution:
        verdict = (view.verification or {}).get("verdict")
        st.caption(f"Resolution recorded ({view.resolution.get('fix_outcome')})"
                   + (f", verification: {verdict}" if verdict else ", not verified yet")
                   + (f", indexed as {view.indexed_doc_id}" if view.indexed_doc_id else "") + ".")
    if output["status"] == "SYSTEM_ERROR":
        system_error_layout(view, output, role)
    else:
        tabs = st.tabs(["Summary", "Hypotheses and evidence", "Changes", "Actions", "Timeline", "Dispatch",
                        "Review"])
        with tabs[0]:
            summary_tab(output)
        with tabs[1]:
            hypotheses_tab(output)
        with tabs[2]:
            changes_tab(output)
        with tabs[3]:
            actions_tab(output)
        with tabs[4]:
            timeline_tab(output)
        with tabs[5]:
            dispatch_section(view, output, role)
        with tabs[6]:
            review_section(view, run_id, output, role)
    st.page_link("pages/3_Resolve_and_Verify.py", label="Resolve and verify this incident", icon="✅")

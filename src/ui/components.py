"""Shared UI pieces (Architecture Spec Section 14; Req. Section 15 UI rules).

Status is always shown as a label inside a coloured band, never by colour
alone, and every simulated dispatch carries a "Simulated" label.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from src import config
from src.logger_setup import configure_logging
from src.schemas.enums import ReviewerRole

from . import state

STATUS_STYLE = {  # label, colour
    "PENDING_REVIEW": ("Pending review", "#d97706"),
    "APPROVED": ("Approved", "#16a34a"),
    "EDITED": ("Edited and approved", "#2563eb"),
    "REJECTED": ("Rejected", "#dc2626"),
    "INSUFFICIENT_EVIDENCE": ("Insufficient evidence", "#7c3aed"),
    "SYSTEM_ERROR": ("System error: no recommendation", "#6b7280"),
}
SEVERITY_COLOUR = {"S1": "#b91c1c", "S2": "#ea580c", "S3": "#ca8a04", "S4": "#64748b"}
HUMAN_RCA_COLOUR = "#c2410c"
LOW_CONFIDENCE = 0.5

NODE_LABELS = {
    "intake": "Intake: read and classify the input",
    "correlate_dedup": "Correlate and deduplicate",
    "redact": "Redact personal data",
    "severity_rules_pass1": "Severity rules, pass 1",
    "dispatch_fast_page": "Critical fast path: page",
    "triage_agent": "Triage Agent",
    "itsm_upsert": "ITSM ticket",
    "severity_rules_pass2": "Severity rules, pass 2",
    "rca_agent": "RCA Agent",
    "change_correlation_agent": "Change Correlation Agent",
    "analysis_join": "Join the analysis",
    "recommendation_agent": "Recommendation Agent",
    "severity_rescore": "Severity re-score",
    "output_guardrails": "Output guardrails",
    "confidence_gate": "Confidence gate",
    "final_severity_gate": "Final severity gate",
    "dispatch_page": "Page the escalation team",
    "dispatch_itsm_assign": "Assign to Production Support",
    "notify": "Notifications",
    "finalize": "Finalize",
    "rules_only_dispatch": "Rules-only dispatch (fallback)",
    "system_error": "System error",
}

_logging_dir: Optional[Path] = None


def setup_page(title: str, icon: str) -> ReviewerRole:
    """First call on every page: page config, settings, logging, session timeout and the sidebar."""
    global _logging_dir
    st.set_page_config(page_title=f"{title} · EventHub AIOps", page_icon=icon, layout="wide")
    settings = config.get_settings()
    settings.apply_to_environment()
    if _logging_dir != settings.logs_dir:
        configure_logging(settings.logs_dir)
        _logging_dir = settings.logs_dir
    if state.touch():
        st.info("Your session was idle for more than "
                f"{settings.ui_session_timeout_min} minutes, so the page selections were cleared.")
    return sidebar()


def sidebar() -> ReviewerRole:
    with st.sidebar:
        st.markdown("**Acting as**")
        roles = [r.value for r in state.ROLE_LABELS]
        st.selectbox("Role", roles, index=roles.index(state.role().value),
                     format_func=lambda v: state.ROLE_LABELS[ReviewerRole(v)], key="role_select",
                     on_change=lambda: state.put(state.ROLE, st.session_state.role_select),
                     help="No sign-in in the prototype. The role is recorded with every decision.")
        st.text_input("Reviewer ID", value=state.reviewer_id(), key="reviewer_id_input", max_chars=40,
                      on_change=lambda: state.put(state.REVIEWER_ID, st.session_state.reviewer_id_input),
                      help="A short ID, not your name. Pairwise labels need two different IDs.")
        settings = config.get_settings()
        if not settings.llm_enabled:
            st.warning("LLM kill switch is on (LLM_ENABLED=false): runs use the rules only.")
        st.caption("Every page, ticket and notification is **simulated**: it is written to the local "
                   "outbox only.")
    return state.role()


def _band(label: str, colour: str, detail: str = "") -> str:
    extra = f'<span style="opacity:.85;margin-left:.75rem">{html.escape(detail)}</span>' if detail else ""
    return (f'<div style="border-left:6px solid {colour};background:{colour}1f;padding:.55rem .9rem;'
            f'border-radius:4px;margin:.25rem 0 .5rem 0"><strong style="color:{colour}">'
            f'{html.escape(label)}</strong>{extra}</div>')


def status_band(status: str, *, needs_human_rca: bool = False, top_confidence: Optional[float] = None,
                detail: str = "") -> None:
    label, colour = STATUS_STYLE.get(status, (status, "#6b7280"))
    st.markdown(_band(f"{label} ({status})", colour, detail), unsafe_allow_html=True)
    if needs_human_rca:
        st.markdown(_band("Needs human RCA", HUMAN_RCA_COLOUR,
                          "The analysis is not confident enough; investigate before acting."),
                    unsafe_allow_html=True)
    elif top_confidence is not None and top_confidence < LOW_CONFIDENCE and status != "SYSTEM_ERROR":
        st.markdown(_band("Low confidence", HUMAN_RCA_COLOUR, f"Top hypothesis confidence {top_confidence:.2f}"),
                    unsafe_allow_html=True)


def severity_badge(level: Optional[str], suffix: str = "") -> str:
    if not level:
        return ""
    colour = SEVERITY_COLOUR.get(level, "#64748b")
    return (f'<span style="background:{colour};color:#fff;padding:.1rem .5rem;border-radius:4px;'
            f'font-weight:600">{html.escape(level)}</span>{html.escape(suffix)}')


def simulated(text: str) -> None:
    st.markdown(f'<span style="border:1px solid #64748b;border-radius:4px;padding:0 .35rem;font-size:.8rem">'
                f'SIMULATED</span> {html.escape(text)}', unsafe_allow_html=True)


def fmt_time(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def evidence_drawer(evidence_ids: list[str], evidence: dict[str, Any], key: str) -> None:
    """One popover per evidence ID: one click from a claim to its source record (Req. 15)."""
    if not evidence_ids:
        st.caption("No evidence cited.")
        return
    per_row = 6
    for start in range(0, len(evidence_ids), per_row):
        row = evidence_ids[start:start + per_row]
        for col, evidence_id in zip(st.columns(per_row), row):
            item = evidence.get(evidence_id)
            with col.popover(evidence_id, width="stretch", disabled=item is None,
                             help=None if item else "Not in this output's evidence"):
                if item:
                    st.markdown(f"**{item['source']}** · {item['service']} · {fmt_time(item['timestamp'])}")
                    st.write(item["summary"])
                    if item.get("record"):
                        st.json(item["record"], expanded=False)


def action_card(action: dict[str, Any]) -> None:
    risk = action.get("risk_level", "")
    colour = {"high": "#dc2626", "medium": "#d97706", "low": "#16a34a"}.get(risk, "#64748b")
    flags = ", ".join(action.get("policy_flags") or [])
    st.markdown(
        f'<div style="border:1px solid {colour}66;border-left:6px solid {colour};border-radius:4px;'
        f'padding:.5rem .8rem;margin-bottom:.5rem"><strong>Step {action["step"]}.</strong> '
        f'{html.escape(action["action"])}<br><span style="color:{colour};font-weight:600">'
        f'Risk: {html.escape(risk)}</span> · type <code>{html.escape(action.get("action_type", ""))}</code> · '
        f'runbook {html.escape(action.get("runbook_citation", ""))}<br>'
        f'<span style="opacity:.85">Expected effect: {html.escape(action.get("expected_effect", ""))}</span>'
        + (f'<br><span style="color:#dc2626">Policy flags: {html.escape(flags)}</span>' if flags else "")
        + '<br><span style="opacity:.75;font-size:.85rem">Advisory only: requires human approval; '
        'nothing is executed by the system.</span></div>', unsafe_allow_html=True)


RATING_FIELDS = {"rca": "Root cause analysis", "actions": "Recommended actions", "severity": "Severity",
                 "summary": "Stakeholder summary"}


def rating_widget(key: str) -> dict[str, int]:
    """Four 1-5 ratings (Req. FR-58)."""
    cols = st.columns(len(RATING_FIELDS))
    return {name: col.select_slider(label, options=[1, 2, 3, 4, 5], value=3, key=f"{key}_{name}")
            for col, (name, label) in zip(cols, RATING_FIELDS.items())}


def incident_label(view: Any) -> str:
    service = view.root_service or "unknown service"
    return f"{view.incident_id} · {service} · {view.state.value}"


def task_panel() -> None:
    """Progress of the current learning-loop task; refreshes itself every 2 s and reruns the page once
    when the task ends, so the page shows the new results."""
    from . import runner

    @st.fragment(run_every=2)
    def panel() -> None:
        task = runner.latest_task(runner.LEARNING)
        if task is None:
            return
        if not task.done.is_set():
            st.info(f"Running: **{task.label}** · {task.message} · {task.elapsed_s:.0f} s")
            if task.total:
                st.progress(min(task.done_count / task.total, 1.0), text=f"{task.done_count} of {task.total}")
            return
        if state.get("task_seen") != task.task_id:
            state.put("task_seen", task.task_id)
            st.rerun()
        if task.error:
            st.error(f"{task.label} failed: {task.error}")
        else:
            st.success(f"{task.label} finished in {task.elapsed_s:.0f} s.")

    panel()


def start_learning_task(label: str, target: Any) -> None:
    """Start a learning-loop task from a button, or explain why it cannot start."""
    from . import runner

    try:
        runner.start_task(runner.LEARNING, label, target, started_by=state.reviewer_id())
        st.rerun()
    except ValueError as exc:
        st.warning(str(exc))


def role_note(allowed: tuple[ReviewerRole, ...], what: str) -> bool:
    """True if the current role may do `what`; otherwise show who can."""
    if state.role() in allowed:
        return True
    st.caption(f"{what}: switch the role in the sidebar to "
               + " or ".join(state.ROLE_LABELS[r] for r in allowed) + ".")
    return False


def json_block(data: Any) -> None:
    st.code(json.dumps(data, indent=1, ensure_ascii=False), language="json")


def _dispatch_text(dispatch: Any) -> str:
    parts = []
    if dispatch.paged:
        parts.append("simulated page sent" + (" (fast path)" if dispatch.fast_path else ""))
    if dispatch.itsm_ticket_id:
        parts.append(f"simulated ticket {dispatch.itsm_ticket_id}")
    if dispatch.assigned_queue:
        parts.append(f"assigned to {dispatch.assigned_queue}")
    if dispatch.notifications:
        parts.append(f"{len(dispatch.notifications)} simulated notifications")
    if dispatch.suppressed:
        parts.append("dispatch suppressed (evaluation mode)")
    return "; ".join(parts)


RULES_KEYS = {"severity_rules_pass1": "rules_pass1", "severity_rules_pass2": "rules_pass2",
              "severity_rescore": "rules_rescore"}
DISPATCH_NODES = ("dispatch_fast_page", "itsm_upsert", "dispatch_page", "dispatch_itsm_assign", "notify",
                  "rules_only_dispatch")


def describe_update(node: str, update: dict[str, Any]) -> str:
    """One line of detail for a finished node, from its state update."""
    if update.get("intake_rejection"):
        return "input rejected"
    if update.get("failed_node") and node not in ("system_error", "rules_only_dispatch"):
        return f"failed ({update.get('error_type').value if update.get('error_type') else 'error'})"
    if node == "correlate_dedup" and update.get("incident") is not None:
        return update["incident"].incident_id + (" (existing incident, deduplicated)" if update.get("deduplicated")
                                                 else "")
    if node in RULES_KEYS and update.get(RULES_KEYS[node]) is not None:
        rules = update[RULES_KEYS[node]]
        return f"{rules.level.value} ({', '.join(r.rule_id for r in rules.rules_fired) or 'no rule fired'})"
    if node == "triage_agent" and update.get("triage") is not None:
        triage = update["triage"]
        return f"issue class {triage.issue_class.value}, proposed {triage.proposed_severity.value}"
    if node == "rca_agent" and update.get("rca") is not None:
        return f"{len(update['rca'].hypotheses)} hypotheses"
    if node == "change_correlation_agent" and update.get("changes") is not None:
        return f"{len(update['changes'].findings)} change findings"
    if node == "recommendation_agent" and update.get("recommendation") is not None:
        return f"{len(update['recommendation'].recommended_actions)} actions proposed"
    if node == "output_guardrails":
        kept = len(update.get("recommended_actions") or [])
        flags = update.get("guardrail_flags") or []
        return f"actions kept: {kept}" + (f"; flags: {', '.join(flags)}" if flags else "")
    if node == "confidence_gate":
        return "needs human RCA" if update.get("needs_human_rca") else "confidence OK"
    if node == "final_severity_gate" and update.get("final_severity") is not None:
        return f"final severity {update['final_severity'].level.value}"
    if node in DISPATCH_NODES:
        dispatch = update.get("dispatch")
        text = _dispatch_text(dispatch) if dispatch is not None else ""
        if node == "dispatch_page" and not text:
            return "added the recommendation to the earlier page (no second page)"
        return text
    if node == "system_error":
        return f"failed step {update.get('failed_node') or 'unknown'}"
    return ""

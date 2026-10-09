"""Adaptation History page (Req. Section 15, FR-31 to FR-34, FR-62, FR-71; H-10).

The SME or the Evaluation Engineer scans the feedback for recurring patterns,
approves or rejects each proposed change, and applies approved ones (golden set
before and after, regression check, held-out pairwise check for preference-driven
changes). Every entry shows its metrics and plain-language explanation, and an
evidence report proves the chain from the logs. Severity signals are listed for
the SME's rules review; the engine never changes severity rules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src import config  # noqa: E402
from src.agent import prompts  # noqa: E402
from src.evaluation import adaptation_engine, evidence_report  # noqa: E402
from src.ui import components, state  # noqa: E402

FLASH = "adapt_flash"
STATUS_COLOUR = {"proposed": "#d97706", "waiting": "#64748b", "approved": "#2563eb", "applied": "#16a34a",
                 "rejected": "#dc2626", "reverted": "#7c3aed"}


def metrics_table(entry) -> None:
    before = {m.metric_name: m.value for m in entry.before_metrics}
    after = {m.metric_name: m.value for m in entry.after_metrics}
    if not before and not after:
        return
    st.dataframe(pd.DataFrame([{"metric": n, "before": before.get(n), "after": after.get(n),
                                "change (points)": round((after[n] - before[n]) * 100, 1)
                                if n in before and n in after else None}
                               for n in sorted(set(before) | set(after))]), hide_index=True, width="stretch")


def proposed_controls(entry, can_decide: bool) -> None:
    change = entry.proposed_change
    if not can_decide:
        return
    with st.form(f"decide_{entry.adaptation_id}"):
        edited = {}
        if change["type"] == "prompt_rule":
            edited["text"] = st.text_area("Guideline to add (edit before approving if needed)", value=change["text"])
        reason = st.text_input("Reason (required to reject)")
        c1, c2 = st.columns(2)
        if c1.form_submit_button("Approve", type="primary"):
            try:
                adaptation_engine.approve(entry.adaptation_id, state.role(), state.reviewer_id(), edited)
                state.put(FLASH, f"{entry.adaptation_id} approved. Apply it to measure the change.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if c2.form_submit_button("Reject"):
            try:
                adaptation_engine.reject(entry.adaptation_id, reason, state.role(), state.reviewer_id())
                state.put(FLASH, f"{entry.adaptation_id} rejected.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def approved_controls(entry, can_decide: bool) -> None:
    session_id = entry.trigger_pattern.get("pairwise_session_id")
    if session_id:
        st.info(f"The golden set passed. Waiting for reviewers to label the held-out pairs of {session_id} "
                "(Feedback and RLHF page).")
        if can_decide and st.button("Finish the preference check", key=f"finish_{entry.adaptation_id}"):
            try:
                done = adaptation_engine.finish_preference_check(entry.adaptation_id)
                state.put(FLASH, f"{entry.adaptation_id} {done.status}.")
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))
        return
    if not can_decide:
        return
    scenario = entry.trigger_pattern.get("scenario_id")
    c1, c2 = st.columns(2)
    judge = c1.toggle("DeepEval judge on", value=True, key=f"judge_{entry.adaptation_id}",
                      help="Off: programmatic metrics only (faster, cheaper, rubric-based RCA accuracy).")
    scope = c2.radio("Golden cases", ["All cases", f"Only {scenario} cases"], key=f"scope_{entry.adaptation_id}",
                     help="The specification asks for the full golden set; the subset is for quick trials.")
    st.caption("Runs the golden set twice (before and after): for all cases about 25 minutes and $0.30 to "
               "$0.50 with the judge.")
    if st.button("Apply", type="primary", key=f"apply_{entry.adaptation_id}"):
        from src.evaluation import golden_dataset

        case_ids = None if scope == "All cases" else [c["case_id"] for c in golden_dataset.cases()
                                                      if c["scenario_id"] == scenario]
        aid = entry.adaptation_id
        components.start_learning_task(
            f"Apply {aid}", lambda task: adaptation_engine.apply(aid, use_judge=judge, case_ids=case_ids,
                                                                 progress=task.report))


def evidence_controls(entry) -> None:
    path = config.get_settings().evidence_dir / f"{entry.adaptation_id}.md"
    if st.button("Generate the evidence report", key=f"evidence_{entry.adaptation_id}"):
        path, missing = evidence_report.generate(entry.adaptation_id)
        st.success(f"Written to {path.parent.name}/{path.name}: "
                   + ("every step found." if not missing else f"missing {', '.join(missing)}."))
    if path.exists():
        with st.expander("Evidence report"):
            st.markdown(path.read_text(encoding="utf-8"))


def entry_view(entry, can_decide: bool) -> None:
    colour = STATUS_COLOUR.get(entry.status, "#64748b")
    change = entry.proposed_change
    title = (f"{entry.adaptation_id} · {entry.status.upper()} · {change.get('type', '').replace('_', ' ')} for "
             f"{change.get('agent')} · {entry.trigger_pattern.get('kind', '').replace('_', ' ')}")
    with st.expander(title, expanded=entry.status in ("proposed", "approved")):
        st.markdown(f'<span style="color:{colour};font-weight:600">{entry.status.upper()}</span> · source '
                    f'{entry.source} · created {components.fmt_time(entry.created_at)}', unsafe_allow_html=True)
        pattern = entry.trigger_pattern
        st.write(f"Pattern: {pattern.get('count')} signals (threshold {pattern.get('threshold')}) from reviewers "
                 f"{pattern.get('reviewers') or 'system'}"
                 + (" · reviewer cap exceeded" if pattern.get("reviewer_cap_exceeded") else ""))
        for reason in pattern.get("reasons", [])[:4]:
            st.caption(f"“{reason}”")
        if change["type"] == "prompt_rule":
            st.markdown(f"**Guideline:** {change['text']}")
        elif change["type"] == "few_shot_example":
            st.code(json.dumps(change["example"], indent=1, ensure_ascii=False)[:1500], language="json")
        else:
            st.markdown(f"**Alias:** `{change.get('alias_key')}` → {change.get('terms')}")
        if entry.explanation:
            st.info(entry.explanation)
        if entry.prompt_version_after:
            st.caption(f"Prompt version {entry.prompt_version_before} → {entry.prompt_version_after}")
        if entry.reject_reason:
            st.warning(f"Rejected: {entry.reject_reason}")
        metrics_table(entry)
        if entry.held_out_win_rate is not None:
            st.write(f"Held-out win rate {entry.held_out_win_rate:.2f} · length change {entry.length_change_pct}%")
        if entry.status == "proposed":
            proposed_controls(entry, can_decide)
        elif entry.status == "approved":
            approved_controls(entry, can_decide)
        if entry.status in ("applied", "reverted", "approved"):
            evidence_controls(entry)


role = components.setup_page("Adaptation History", "🧪")
st.title("Adaptation history")
st.caption("Changes are limited to prompt guidelines, few-shot examples and retrieval aliases, and nothing goes "
           "live without approval and a before/after check on the golden set.")
if state.get(FLASH):
    st.success(state.get(FLASH))
    state.put(FLASH, None)
components.task_panel()
can_decide = components.role_note(adaptation_engine.APPROVER_ROLES,
                                  "Scanning, approving and applying are done by the SME or the Evaluation Engineer")
if can_decide and st.button("Scan feedback for patterns", key="scan"):
    with st.spinner("Scanning and drafting proposals…"):
        found = adaptation_engine.scan_and_propose(role)
    state.put(FLASH, f"{sum(e.status == 'proposed' for e in found)} proposed, "
                     f"{sum(e.status == 'waiting' for e in found)} waiting for more signals.")
    st.rerun()
st.markdown("Active prompt versions: " + ", ".join(f"{a} `{v}`" for a, v in prompts.active_version_set().items()))
entries = adaptation_engine.entries()
if not entries:
    st.info("No adaptations yet. Promote reviewer feedback on the Review Queue page, then scan.")
for status in ("proposed", "approved", "applied", "reverted", "rejected", "waiting"):
    group = [e for e in entries if e.status == status]
    if group:
        st.subheader(f"{status.capitalize()} ({len(group)})")
        for entry in reversed(group):
            entry_view(entry, can_decide)
st.subheader("Rules review (for the SME)")
st.caption("Severity comes from the rules engine, so these signals are never adapted automatically. The SME "
           "decides whether to change data/severity_rules.yaml.")
summary = adaptation_engine.rules_review_summary()
if summary:
    st.dataframe(pd.DataFrame([{**r, "examples": " | ".join(r["examples"])} for r in summary]), hide_index=True,
                 width="stretch")
else:
    st.caption("No severity signals.")
changes = adaptation_engine.rules_change_log()
if changes:
    st.markdown("**Rules change log**")
    st.dataframe(pd.DataFrame(changes), hide_index=True, width="stretch")

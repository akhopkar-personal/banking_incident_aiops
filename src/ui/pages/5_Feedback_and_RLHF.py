"""Feedback and RLHF page (Req. Section 15, FR-59 to FR-64; H-9).

Pairwise sessions compare two prompt versions of one agent on golden cases.
Reviewers label the pairs blind (random order, versions hidden); two reviewers
per pair, the SME breaks ties. The page also shows the reward score per prompt
version set, with its sample size, and exports agreed pairs in DPO format.
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
from src.evaluation import adaptation_engine, golden_dataset  # noqa: E402
from src.feedback import dpo_exporter, pairwise_session, preference_store, reward_model  # noqa: E402
from src.schemas.enums import ReviewerRole  # noqa: E402
from src.ui import components, state  # noqa: E402

FLASH = "rlhf_flash"
CREATORS = (ReviewerRole.EVALUATION_ENGINEER, ReviewerRole.SME)
AGENT_LABELS = {"triage_agent": "Triage", "rca_agent": "Root Cause Analysis",
                "change_correlation_agent": "Change Correlation", "recommendation_agent": "Recommendation"}


def render_part(part: dict) -> None:
    """One side of a pair, readable without revealing its version."""
    if not part:
        st.caption("(no output: the run ended without this agent's result)")
        return
    for h in part.get("hypotheses") or []:
        st.markdown(f"**#{h['rank']}** ({h['confidence']:.2f}) {h['root_cause']}")
        st.caption("Evidence: " + ", ".join(h.get("supporting_evidence") or []))
    for f in part.get("change_findings") or []:
        rank = f.get("linked_hypothesis_rank")
        st.markdown(f"**{f['change_id']}** {f'linked to #{rank}' if rank else 'not linked'}: {f['rationale']}")
    for a in part.get("recommended_actions") or []:
        st.markdown(f"{a['step']}. {a['action']} · *{a['risk_level']}* · {a['runbook_citation']}")
    if part.get("stakeholder_summary"):
        st.info(part["stakeholder_summary"])
    if part.get("issue_class"):
        st.markdown(f"Class `{part['issue_class']}` · severity {(part.get('severity') or {}).get('level')}")
    rest = {k: v for k, v in part.items() if k in ("timeline", "affected_services", "regulatory_notes",
                                                   "insufficient_evidence_reason")}
    if rest:
        with st.expander("More"):
            st.code(json.dumps(rest, indent=1, ensure_ascii=False, default=str), language="json")


def label_tab(role: ReviewerRole) -> None:
    ready = [s for s in pairwise_session.sessions() if s["status"] == "ready"]
    if not ready:
        st.info("No pairwise session yet. The Evaluation Engineer creates one in the Sessions tab.")
        return
    by_id = {s["session_id"]: s for s in ready}
    sid = st.selectbox("Session", list(by_id), key="label_session",
                       format_func=lambda i: f"{i} · {AGENT_LABELS[by_id[i]['agent']]} · "
                                             f"{len(by_id[i]['pairs'])} pairs")
    session = by_id[sid]
    pair_id = pairwise_session.next_pair_for(session, state.reviewer_id(), role)
    if pair_id is None:
        st.success(f"Nothing left for {state.reviewer_id()} to label in {sid}. Another reviewer ID, or the SME for "
                   "disputed pairs, may still have pairs to label.")
        return
    p = pairwise_session.pair(session, pair_id)
    status = preference_store.agreement(pair_id)
    st.markdown(f"**{pair_id}** · case {p['case_id']} · "
                + ("**disputed: SME tie-break**" if status == "disputed" else "versions hidden, order random"))
    left, right = pairwise_session.shown(session, pair_id)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Output 1")
        render_part(left)
    with c2:
        st.markdown("#### Output 2")
        render_part(right)
    with st.form(f"label_{pair_id}"):
        choice = st.radio("Which is better?", ["left", "right", "tie"], horizontal=True,
                          format_func={"left": "Output 1", "right": "Output 2", "tie": "About the same"}.get)
        reason = st.text_input("Why? (required)")
        if st.form_submit_button("Record my choice", type="primary"):
            try:
                pairwise_session.label(sid, pair_id, choice, reason, role, state.reviewer_id())
                state.put(FLASH, f"Label recorded for {pair_id}.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def sessions_tab(role: ReviewerRole) -> None:
    if components.role_note(CREATORS, "Creating a session is done by the Evaluation Engineer or the SME"):
        versions = prompts.load_versions()
        with st.form("new_session"):
            agent = st.selectbox("Agent", list(AGENT_LABELS), format_func=AGENT_LABELS.get, index=1)
            names = list(versions[agent]["versions"])
            c1, c2 = st.columns(2)
            version_a = c1.selectbox("Version A", names, index=0)
            version_b = c2.selectbox("Version B", names, index=len(names) - 1)
            cases = golden_dataset.cases()
            default = [c["case_id"] for c in cases if c["variant"] == "replay"]
            chosen = st.multiselect("Golden cases (each runs twice: once per version)", [c["case_id"] for c in cases],
                                    default=default)
            share = st.slider("Held-out share (measures the win rate; never used to propose changes)", 0.0, 1.0,
                              0.5, 0.1)
            open_adaptations = [e.adaptation_id for e in adaptation_engine.entries()
                                if e.status in ("approved", "applied")]
            linked = st.selectbox("For adaptation (optional)", [""] + open_adaptations)
            st.caption(f"About $0.004 and a minute per run; {len(chosen) * 2} runs, "
                       f"{config.get_settings().eval_concurrency} at a time.")
            if st.form_submit_button("Create session", type="primary"):
                if not chosen:
                    st.error("Choose at least one case.")
                else:
                    components.start_learning_task(
                        f"Pairwise session {AGENT_LABELS[agent]} {version_a} vs {version_b}",
                        lambda task: pairwise_session.create_session(
                            agent, version_a, version_b, chosen, held_out_share=share,
                            adaptation_id=linked or None, created_by=role, created_by_id=state.reviewer_id(),
                            progress=lambda done, total, case: task.report(f"{case}", done, total)))
    rows = []
    for s in pairwise_session.sessions():
        r = pairwise_session.result(s["session_id"]) if s["pairs"] else {}
        rows.append({"session": s["session_id"], "agent": s["agent"], "A": s["version_a"], "B": s["version_b"],
                     "adaptation": s.get("adaptation_id") or "", "status": s["status"], "pairs": len(s["pairs"]),
                     "agreed": r.get("agreed", 0), "disputed": r.get("disputed", 0),
                     "SME decided": r.get("tie_broken", 0),
                     "win rate B (held-out)": r.get("win_rate_b"), "agreement": r.get("agreement_rate")})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        sid = st.selectbox("Log a session's result to eval.log", [r["session"] for r in rows], key="summarize_pick")
        if st.button("Log result", key="summarize"):
            outcome = pairwise_session.summarize(sid)
            st.success(f"{sid}: win rate of B {outcome['win_rate_b']} over {outcome['decided_pairs_counted']} "
                       "held-out pairs, logged.")
        errors = [e for s in pairwise_session.sessions() for e in s.get("errors", [])]
        if errors:
            with st.expander(f"{len(errors)} failed runs"):
                st.write(errors)


def reward_tab() -> None:
    st.caption("Reward = 0.35 × mean rating + 0.30 × pairwise win rate + 0.15 × approval rate + 0.20 × fix "
               "outcome rate; missing parts are left out and the rest re-weighted. Fewer than "
               "10 signals: not enough feedback.")
    rows = []
    for reward in reward_model.compute_all(log=False):
        c = reward.components
        rows.append({"prompt versions": reward.prompt_version_set_key.replace("|", " · "),
                     "reward": (f"{reward.score:.2f}" if reward.score is not None else "none")
                     if reward.sufficient else "not enough feedback",
                     "signals": reward.signal_count, "mean rating": c.mean_rating, "win rate": c.win_rate,
                     "approval": c.approval_rate, "fix outcome": c.fix_outcome_rate})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if st.button("Log reward scores to eval.log and LangFuse", key="log_rewards"):
        reward_model.compute_all(log=True)
        st.success("Reward scores logged.")


def dpo_tab() -> None:
    st.caption("Agreed pairs with a winner, as prompt / chosen / rejected JSON lines for a later preference "
               "fine-tune (stretch). Ties and disputed pairs are left out; every line is PII-checked.")
    if st.button("Export DPO file", key="dpo"):
        path, count = dpo_exporter.export()
        st.success(f"{count} pairs written to data/feedback/dpo_export/{path.name}.")


role = components.setup_page("Feedback and RLHF", "⚖️")
st.title("Feedback and RLHF")
if state.get(FLASH):
    st.success(state.get(FLASH))
    state.put(FLASH, None)
components.task_panel()
tabs = st.tabs(["Label pairs", "Sessions", "Reward scores", "DPO export"])
with tabs[0]:
    label_tab(role)
with tabs[1]:
    sessions_tab(role)
with tabs[2]:
    reward_tab()
with tabs[3]:
    dpo_tab()

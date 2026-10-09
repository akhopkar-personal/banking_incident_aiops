"""Evaluation page (Req. Section 15, FR-24; Architecture Spec Section 11.1).

Run the golden set (DeepEval judge on or off) and the fixture checks, see the
latest metrics against their targets, the per-case results, latency and cost per
stage, and links to each run's LangFuse trace.
"""

from __future__ import annotations

import sys
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src import config  # noqa: E402
from src.evaluation import deepeval_harness, golden_dataset, langfuse_tracker  # noqa: E402
from src.schemas.enums import ReviewerRole  # noqa: E402
from src.ui import components  # noqa: E402

RUNNERS = (ReviewerRole.EVALUATION_ENGINEER, ReviewerRole.SME)
SHOWN = ("severity_match", "triage_class_match", "rca_top1", "change_hit", "red_herring_rejected",
         "retrieval_recall_at_5", "citation_validity", "abstention_correct", "faithfulness", "hallucination")


def run_controls() -> None:
    if not components.role_note(RUNNERS, "Running evaluations is done by the Evaluation Engineer or the SME"):
        return
    cases = golden_dataset.cases()
    scenarios = sorted({c["scenario_id"] for c in cases})
    c1, c2, c3 = st.columns(3)
    judge = c1.toggle("DeepEval judge on", value=True, key="eval_judge",
                      help="Off: programmatic metrics only; RCA accuracy then uses the evidence rubric.")
    scope = c2.selectbox("Cases", ["All cases"] + scenarios, key="eval_scope")
    c3.caption(f"{len(cases)} cases in {golden_dataset.version()}; all cases take about 12 minutes and "
               "$0.15 to $0.25 with the judge.")
    selected = None if scope == "All cases" else [c["case_id"] for c in cases if c["scenario_id"] == scope]
    b1, b2 = st.columns(2)
    if b1.button("Run the golden set", type="primary", key="run_golden"):
        components.start_learning_task(
            f"Golden set ({scope}, judge {'on' if judge else 'off'})",
            lambda task: deepeval_harness.run_golden_set(
                case_ids=selected, use_judge=judge,
                progress=lambda done, total, case: task.report(case, done, total)))
    if b2.button("Run the fixture checks", key="run_fixtures"):
        components.start_learning_task(
            "Fixture checks", lambda task: deepeval_harness.run_fixtures(
                progress=lambda done, total, name: task.report(name, done, total)))


def targets_table(latest: dict, previous: dict | None) -> None:
    agg = latest["aggregate"]
    before = (previous or {}).get("aggregate", {})
    rows = []
    for name, (target, higher) in deepeval_harness.TARGETS.items():
        value = agg.get(name)
        met = None if value is None else (value >= target if higher else value <= target)
        rows.append({"metric": name, "value": value, "target": f"{'≥' if higher else '≤'} {target}",
                     "met": "n/a" if met is None else ("yes" if met else "NO"),
                     "previous run": before.get(name)})
    for name in ("red_herring_rejected", "severity_within_1", "contextual_recall", "latency_total_s", "cost_usd"):
        if agg.get(name) is not None:
            rows.append({"metric": name, "value": agg[name], "target": "", "met": "", "previous run": before.get(name)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def cases_table(latest: dict) -> None:
    rows = [{"case": c["case_id"], "status": c["status"],
             **{n: c["metrics"].get(n) for n in SHOWN},
             "seconds": c["metrics"].get("latency_total_s"), "cost": c["metrics"].get("cost_usd"),
             "trace": c.get("langfuse_trace_id") or ""} for c in latest["cases"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def stages_table(latest: dict) -> None:
    stages: dict[str, list[int]] = {}
    for c in latest["cases"]:
        for stage, ms in (c.get("latency_ms_per_stage") or {}).items():
            stages.setdefault(stage, []).append(ms)
    if stages:
        st.dataframe(pd.DataFrame([{"stage": s, "mean ms": round(mean(v)), "max ms": max(v), "runs": len(v)}
                                   for s, v in sorted(stages.items(), key=lambda kv: -mean(kv[1]))]),
                     hide_index=True, width="stretch")
    costs = [c["metrics"].get("cost_usd") or 0 for c in latest["cases"]]
    st.caption(f"Total agent cost for this run ${sum(costs):.4f} (judge calls not included).")


def trace_links(latest: dict) -> None:
    settings = config.get_settings()
    if not settings.langfuse_configured():
        st.caption("LangFuse is not configured, so there are no traces.")
        return
    st.markdown(f"LangFuse: [{settings.langfuse_host}]({settings.langfuse_host}) · dataset "
                f"`{settings.langfuse_dataset_name}`")
    traced = [c for c in latest["cases"] if c.get("langfuse_trace_id")]
    if traced:
        case = st.selectbox("Open a case's trace", [c["case_id"] for c in traced], key="trace_pick")
        if st.button("Get the trace link", key="trace_link"):
            trace = next(c for c in traced if c["case_id"] == case)["langfuse_trace_id"]
            url = langfuse_tracker.trace_url(trace)
            st.markdown(f"[Open the trace for {case}]({url})" if url else f"Trace ID `{trace}`")


components.setup_page("Evaluation", "📊")
st.title("Evaluation")
components.task_panel()
run_controls()
golden_runs = deepeval_harness.saved_results("golden")
if not golden_runs:
    st.info("No golden-set run yet.")
else:
    latest = golden_runs[0]
    st.subheader(f"Latest golden-set run: {latest['eval_id']}")
    st.caption(f"{latest['phase']} · {latest['golden_dataset_version']} · judge "
               f"{'on' if latest['use_judge'] else 'off'} · {int(latest['aggregate']['cases'])} cases · prompt "
               f"versions {latest['prompt_version_set']}"
               + (f" · adaptation {latest['adaptation_id']}" if latest.get("adaptation_id") else ""))
    targets_table(latest, golden_runs[1] if len(golden_runs) > 1 else None)
    st.subheader("Per case")
    cases_table(latest)
    st.subheader("Latency and cost per stage")
    stages_table(latest)
    trace_links(latest)
    with st.expander(f"Earlier runs ({len(golden_runs) - 1})"):
        st.dataframe(pd.DataFrame([{"eval": r["eval_id"], "phase": r["phase"], "adaptation": r.get("adaptation_id"),
                                    "judge": r["use_judge"], "cases": r["aggregate"].get("cases"),
                                    **{n: r["aggregate"].get(n) for n in ("rca_top1", "severity_match", "change_hit",
                                                                          "faithfulness")}}
                                   for r in golden_runs[1:]]), hide_index=True, width="stretch")
fixtures = deepeval_harness.saved_results("fixtures")
if fixtures:
    st.subheader(f"Fixture checks: {fixtures[0]['passed']} of {fixtures[0]['total']} passed")
    st.dataframe(pd.DataFrame(fixtures[0]["checks"]), hide_index=True, width="stretch")

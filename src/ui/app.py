"""Investigate page: the Streamlit app's entry point (Req. Section 15; Architecture Spec Section 14).

    streamlit run src/ui/app.py

Choose the scenario whose telemetry to read, give a detection replay, an alert
JSON or a free-text report, and watch the graph run node by node. The early
result (rules severity, issue class, fast-path page) appears as soon as it is known.
"""

from __future__ import annotations

import json
import sys
import time
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st  # noqa: E402

from src import config  # noqa: E402
from src.schemas.enums import RunMode  # noqa: E402
from src.services import incident_state_store as store  # noqa: E402
from src.ui import components, runner, state  # noqa: E402

LATEST_JOB = "latest_job"  # the latest run of this session, running or finished
SEEN_JOB = "seen_job"  # the job whose outcome has already been taken into the session
MODES = {"Detection replay": "replay", "Alert JSON": "alert_json", "Free text": "free_text"}


@lru_cache(maxsize=None)
def manifest(scenario_id: str) -> dict:
    return json.loads((config.get_settings().scenario_dir(scenario_id) / "manifest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def examples() -> dict[tuple[str, str], str]:
    """The first golden alert JSON and free-text input for each scenario, as starting text."""
    path = config.get_settings().reference_file("test_inputs.json")
    found: dict[tuple[str, str], str] = {}
    for case in json.loads(path.read_text(encoding="utf-8")):
        found.setdefault((case["scenario_id"], case["input_mode"]), case["raw_input"])
    return found


def scenario_label(scenario_id: str) -> str:
    data = manifest(scenario_id)
    kind = " (fixture)" if data.get("kind") != "scenario" else ""
    return f"{scenario_id} · {data.get('title', '')}{kind}"


def input_form(running: bool) -> None:
    settings = config.get_settings()
    scenario_id = st.selectbox("Data source: the scenario whose telemetry the tools read", list(config.SCENARIO_DIRS),
                               format_func=scenario_label, key="scenario_id")
    data = manifest(scenario_id)
    mode = MODES[st.radio("Input", list(MODES), horizontal=True, key="input_mode")]
    raw = ""
    if mode == "replay":
        st.caption(f"Detection replay: the anomaly detector reads the telemetry from {data['window_start']} "
                   f"to {data['window_end']}.")
    else:
        template = json.dumps({"alert_name": "", "service": "", "timestamp": data["reference_time"], "severity": ""})
        default = examples().get((scenario_id, mode), template if mode == "alert_json" else "")
        raw = st.text_area("Alert JSON" if mode == "alert_json" else "What is going wrong, and since when?",
                           value=default, height=120, key=f"raw_{scenario_id}_{mode}",
                           help="Alert JSON needs `service` and `timestamp`. Free text needs a clock time "
                                "(read on the scenario's date) unless you set a time window.")
    window = None
    if st.checkbox("Set the time window", key="use_window"):
        c1, c2 = st.columns(2)
        start = c1.text_input("Window start (ISO 8601, UTC)", value=data["window_start"], key=f"ws_{scenario_id}")
        end = c2.text_input("Window end (ISO 8601, UTC)", value=data["window_end"], key=f"we_{scenario_id}")
        window = {"start": start.strip(), "end": end.strip()}
    evaluation = st.toggle("Evaluation mode: decide everything but dispatch nothing", key="evaluation_mode")
    if settings.llm_enabled and settings.openai_api_key is None:
        st.warning("OPENAI_API_KEY is not set: the agents cannot run, so the run will fall back to rules-only "
                   "dispatch and end as SYSTEM_ERROR.")
    if st.button("Investigate", type="primary", key="run", disabled=running):
        job = runner.start(raw_input=raw if mode != "replay" else "", scenario_id=scenario_id,
                           supplied_window=window, run_mode=RunMode.EVALUATION if evaluation else RunMode.LIVE)
        state.put(LATEST_JOB, job.job_id)
        state.put(state.LAST_RUN_ID, None)
        state.put(state.LAST_REJECTION, None)
        st.rerun()


def show_progress(job: runner.Job) -> None:
    """Node-by-node progress and the early result panel, polled until the run ends."""
    left, right = st.columns([3, 2])
    with left:
        status = st.status("Investigating…", expanded=True)
        lines = status.container()
    with right:
        st.markdown("**Early result**")
        early = st.empty()
    shown = 0
    while True:
        finished = job.done.is_set()
        for event in job.events[shown:]:
            detail = f": {event['detail']}" if event["detail"] else ""
            lines.markdown(f"✔ **{event['label']}**{detail} · {event['elapsed_s']} s")
            shown += 1
        with early.container():
            for name in ("Incident", "Rules severity", "Issue class", "Fast-path page"):
                st.markdown(f"{name}: **{job.early.get(name, '…')}**")
        if finished:
            break
        last = job.events[-1]["label"] if job.events else "Starting"
        status.update(label=f"Investigating… {last} ({job.elapsed_s:.0f} s)")
        time.sleep(0.25)
    kind = runner.outcome(job)
    if kind == "output":
        status.update(label=f"Finished in {job.elapsed_s:.1f} s", state="complete", expanded=False)
    else:
        status.update(label="The input was not accepted" if kind == "rejection" else "The run failed",
                      state="error", expanded=False)
    if state.get(SEEN_JOB) != job.job_id:  # first time seen finished: keep the outcome, redraw the form enabled
        state.put(SEEN_JOB, job.job_id)
        if kind == "output":
            state.put(state.LAST_RUN_ID, job.result.run_id)
            state.select_incident(job.result.incident_id, job.result.run_id)
        else:
            state.put(state.LAST_REJECTION, job.result.message if kind == "rejection" else job.error)
        st.rerun()


def show_result(run_id: str) -> None:
    output = store.load_output(run_id)
    if output is None:
        return
    st.subheader(f"Result: {output['incident_id']}")
    hypotheses = output.get("hypotheses") or []
    components.status_band(output["status"], needs_human_rca=bool(output.get("needs_human_rca")),
                           top_confidence=hypotheses[0]["confidence"] if hypotheses else None,
                           detail=f"Incident state {output['incident_state']}")
    dispatch = output.get("dispatch") or {}
    if output["status"] == "SYSTEM_ERROR":
        st.error((output.get("error_detail") or {}).get("message", "The investigation could not complete."))
        rules = output.get("rules_severity") or {}
        st.markdown(f"Rules severity: {components.severity_badge(rules.get('level'))}", unsafe_allow_html=True)
    else:
        severity = output.get("severity") or {}
        st.markdown(f"Severity {components.severity_badge(severity.get('level'))} · issue class "
                    f"**{output.get('issue_class')}**", unsafe_allow_html=True)
        if hypotheses:
            st.markdown(f"Top hypothesis ({hypotheses[0]['confidence']:.2f}): {hypotheses[0]['root_cause']}")
        if output.get("insufficient_evidence_reason"):
            st.info(output["insufficient_evidence_reason"])
    if dispatch.get("paged"):
        components.simulated("Page sent to the Incident Escalation Team"
                             + (" through the critical fast path." if dispatch.get("fast_path") else "."))
    if dispatch.get("itsm_ticket_id"):
        components.simulated(f"ITSM ticket {dispatch['itsm_ticket_id']}"
                             + (f", assigned to {dispatch['assigned_queue']}." if dispatch.get("assigned_queue")
                                else "."))
    if dispatch.get("suppressed"):
        st.caption("Evaluation mode: the dispatch decisions were recorded but nothing was sent.")
    meta = output.get("metadata") or {}
    st.caption(f"Run {run_id} · {sum((meta.get('token_counts') or {}).values())} tokens · "
               f"${meta.get('cost_estimate_usd') or 0:.4f}"
               + (f" · LangFuse trace {meta['langfuse_trace_id']}" if meta.get("langfuse_trace_id") else ""))
    if st.button("Open the incident", type="primary", key="open_incident"):
        st.switch_page("pages/1_Incident.py")


components.setup_page("Investigate", "🔎")
st.title("Investigate an incident")
st.caption("EventHub AIOps · banking incident investigation. The agents analyse; deterministic rules decide "
           "severity and dispatch; every recommendation waits for human review.")
job = runner.get(state.get(LATEST_JOB))  # None if the server restarted since
input_form(running=job is not None and not job.done.is_set())
if job is not None:
    show_progress(job)
if state.get(state.LAST_REJECTION):
    st.warning(state.get(state.LAST_REJECTION))
if state.get(state.LAST_RUN_ID):
    show_result(state.get(state.LAST_RUN_ID))

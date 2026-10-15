"""The flow log of one investigation run: every log line that carries its run_id,
from detection to the human steps after the run, in time order.

    python scripts/show_flow_log.py RUN-...

Nothing new is logged for it. The three JSON log files (interactions, error,
eval; Architecture Spec Section 13) already tag each run-scoped line with
incident_id and run_id; this module collects those lines, places each in one of
the five stages of the architecture and describes it in one sentence. The lines
were redacted when they were written.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.logger_setup import LOG_FILES, read_log

STAGES = ("Detect", "Triage", "Analyse", "Respond", "Close and learn")
OTHER = "Other"
_STAGE_OF_COMPONENT = {
    "Detect": ("intake", "anomaly_detector", "alert_correlator", "correlate_dedup", "redact"),
    "Triage": ("severity_rules_pass1", "dispatch_fast_page", "triage_agent", "itsm_upsert", "severity_rules_pass2"),
    "Analyse": ("rca_agent", "change_correlation_agent", "analysis_join", "recommendation_agent"),
    "Respond": ("severity_rescore", "output_guardrails", "confidence_gate", "final_severity_gate", "dispatch_page",
                "dispatch_itsm_assign", "notify", "finalize", "rules_only_dispatch", "rules_only_runner", "system_error"),
    "Close and learn": ("review_store", "resolution", "resolution_indexer", "feedback_loop", "preference_store",
                        "pairwise_session", "reward_model", "deepeval_harness", "adaptation_engine",
                        "dpo_exporter"),
}
STAGE_OF = {component: stage for stage, components in _STAGE_OF_COMPONENT.items() for component in components}
# Fields every line has; the rest are the event's own details.
COMMON_FIELDS = ("timestamp", "level", "event", "component", "incident_id", "run_id", "trace_id",
                 "langfuse_trace_id", "prompt_version_set", "stack_trace")
ADDED_FIELDS = ("log_file", "flow_stage", "flow_elapsed_s")  # added by load(); never written to the logs


def load(run_id: str, logs_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """Every line of the three logs (rotated files included) for `run_id`, oldest first. Each line
    gets ADDED_FIELDS: `log_file`, `flow_stage` and `flow_elapsed_s` (seconds since the run's first line)."""
    entries = [{**line, "log_file": name} for name in LOG_FILES for line in read_log(name, logs_dir)
               if line.get("run_id") == run_id]
    entries.sort(key=lambda e: e.get("timestamp", ""))
    start = _time(entries[0]) if entries else None
    for entry in entries:
        entry["flow_stage"] = stage_of(entry)
        moment = _time(entry)
        entry["flow_elapsed_s"] = round((moment - start).total_seconds(), 2) if moment and start else None
    return entries


def stage_of(entry: dict[str, Any]) -> str:
    return STAGE_OF.get(entry.get("component", ""), STAGE_OF.get(entry.get("caller", ""), OTHER))


def describe(entry: dict[str, Any]) -> str:
    """One sentence for the line; unknown events list their own fields."""
    e, event = entry, entry.get("event")
    if event == "anomaly_detected":
        return (f"{e.get('service')} {e.get('signal')}: observed {e.get('observed')} against baseline "
                f"{e.get('baseline')} (z {e.get('z_score')}, {e.get('method')})")
    if event == "incident_created":
        return f"new incident for {e.get('root_service')} from {e.get('anomaly_count')} anomalies"
    if event == "node_completed":
        return f"step finished in {e.get('latency_ms')} ms"
    if event == "severity_rules_evaluated":
        fired = ", ".join(e.get("rules_fired") or []) or "none"
        return f"{e.get('pass_name')}: {e.get('severity')} ({e.get('group')}); rules fired: {fired}"
    if event == "tool_call":
        if e.get("status") not in (None, "ok"):  # FR-76: a rejected or failed call
            return f"tool {e.get('tool')} {e['status'].replace('_', ' ')}" + (f": {e['error']}" if e.get("error") else "")
        return f"tool {e.get('tool')} over {e.get('transport')}, {e.get('latency_ms')} ms"
    if event == "tool_access_blocked":
        return f"tool {e.get('tool')} blocked for {e.get('caller')}: {e.get('reason')}"
    if event == "request_refused":
        return f"request refused ({str(e.get('category', '')).replace('_', ' ')}); nothing was investigated"
    if event == "llm_call":
        return (f"{e.get('model')} ({e.get('kind')}): {e.get('input_tokens')} tokens in, {e.get('output_tokens')} "
                f"out, {e.get('latency_ms')} ms, finish {e.get('finish_reason')}")
    if event == "fast_path_page":
        return f"fast-path page ({e.get('mode')}) to {e.get('team')}, {e.get('outbox_id')}"
    if event == "dispatch_page":
        return f"page ({e.get('mode')}), {e.get('outbox_id')}"
    if event == "dispatch_itsm":
        return f"ITSM {e.get('operation')} {e.get('ticket_id')}" + (" (created)" if e.get("created") else "")
    if event == "notification_sent":
        return f"{e.get('channel')} notification to {e.get('audience')} ({e.get('severity')}), {e.get('outbox_id')}"
    if event == "guardrail_block":
        return "guardrail flags: " + ", ".join(e.get("flags") or [])
    if event == "node_retry":
        return f"retry {e.get('attempt')} of {e.get('what')}: {e.get('error_type')}"
    if event == "system_error":
        where = e.get("failed_node") or e.get("component")
        return f"failed in {where}: {e.get('error_type') or e.get('error_type_name')}" + (
            f" ({e['error_message']})" if e.get("error_message") else "")
    if event == "review_decision":
        return f"{e.get('reviewer_role')} review: {e.get('decision')} → {e.get('status')}" + (
            f"; reason: {e['reason']}" if e.get("reason") else "")
    if event == "rating_recorded":
        ratings = ", ".join(f"{k} {v}" for k, v in (e.get("ratings") or {}).items())
        return f"ratings {ratings} (mean {e.get('mean')})"
    if event == "resolution_recorded":
        return f"resolution recorded: fix {e.get('fix_outcome')}, {e.get('resolution_time_min')} min"
    if event == "root_cause_verified":
        return f"root cause verification: {e.get('verdict')}"
    if event == "resolution_indexed":
        return f"indexed into the knowledge base as {e.get('doc_id')} ({e.get('chunks')} chunks)"
    details = {k: v for k, v in e.items() if k not in COMMON_FIELDS and k not in ADDED_FIELDS}
    return "; ".join(f"{k} {_short(v)}" for k, v in details.items())


def summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts for the header of the log."""
    llm = [e for e in entries if e.get("event") == "llm_call"]
    run_lines = [e for e in entries if e["flow_stage"] != "Close and learn"]
    return {
        "lines": len(entries),
        "run_s": run_lines[-1]["flow_elapsed_s"] if run_lines else None,
        "llm_calls": len(llm),
        "tokens": sum((e.get("input_tokens") or 0) + (e.get("output_tokens") or 0) for e in llm),
        "tool_calls": sum(e.get("event") == "tool_call" for e in entries),
        "rejected_tool_calls": sum((e.get("event") == "tool_call" and e.get("status") not in (None, "ok"))
                                   or e.get("event") == "tool_access_blocked" for e in entries),
        "errors": sum(e.get("level") == "ERROR" for e in entries),
    }


def to_text(entries: list[dict[str, Any]]) -> str:
    """A plain-text log, one line per entry, for download or the terminal."""
    if not entries:
        return ""
    first = entries[0]
    lines = [f"Flow log of {first.get('run_id')}, incident {_incident(entries)}", ""]
    for e in entries:
        elapsed = f"+{e['flow_elapsed_s']:.2f}s" if e.get("flow_elapsed_s") is not None else ""
        lines.append(f"{e.get('timestamp', '')[11:23]} {elapsed:>10} {e.get('level', ''):7} "
                     f"[{e['flow_stage']}] {e.get('component')} | {e.get('event')}: {describe(e)}")
    return "\n".join(lines) + "\n"


def to_jsonl(entries: list[dict[str, Any]]) -> str:
    """The original log lines (without the added fields), one JSON object per line."""
    return "".join(json.dumps({k: v for k, v in e.items() if k not in ADDED_FIELDS},
                              ensure_ascii=False) + "\n" for e in entries)


def _incident(entries: list[dict[str, Any]]) -> str:
    return next((e["incident_id"] for e in entries if e.get("incident_id")), "unknown")


def _time(entry: dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(entry["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None


def _short(value: Any, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"

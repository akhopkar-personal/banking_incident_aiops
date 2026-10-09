"""Evidence report for one adaptation (Architecture Spec Section 11.4; Req. FR-71, Section 16.1).

    python -m src.evaluation.evidence_report --adaptation-id ADP-001

Follows the adaptation from the first human feedback to the improved result
using only the three log files and data/feedback/, and writes
docs/evidence/<adaptation_id>.md. A step that cannot be found is marked MISSING.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from src import config
from src.logger_setup import log_eval

STEPS = [
    ("E-1", "A human gave feedback on a real output", "interactions"),
    ("E-2", "The feedback was captured", "interactions"),
    ("E-3", "The SME validated it", "interactions"),
    ("E-4", "A baseline was measured", "eval"),
    ("E-5", "A recurring pattern was detected", "interactions"),
    ("E-6", "A human approved the change", "interactions"),
    ("E-7", "Behavior actually changed", "interactions"),
    ("E-8", "The target metric improved without regressions", "eval"),
    ("E-9", "Humans prefer the new behavior", "eval"),
    ("E-10", "New runs use the new version and are reviewed", "interactions"),
]
KEY_FIELDS = ("incident_id", "run_id", "feedback_id", "decision", "issue_type", "golden_dataset_version", "phase",
              "eval_id", "count", "threshold", "reviewer_role", "prompt_version_before", "prompt_version_after",
              "regression_check", "max_regression_points", "win_rate_new", "reward_score", "signal_count", "reason")


def read_log(name: str) -> list[dict[str, Any]]:
    """Every JSON line of a log, oldest rotated file first."""
    folder = config.get_settings().logs_dir
    files = sorted(folder.glob(f"{name}.log.*"), key=lambda p: -int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0)
    lines: list[dict[str, Any]] = []
    for path in [*files, folder / f"{name}.log"]:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    lines.append(json.loads(line))
                except ValueError:
                    continue
    return lines


def _first(lines: list[dict[str, Any]], match: Callable[[dict[str, Any]], bool]) -> Optional[dict[str, Any]]:
    return next((line for line in lines if match(line)), None)


def build(adaptation_id: str) -> dict[str, Any]:
    from src.evaluation.adaptation_engine import get

    entry = get(adaptation_id)
    interactions, evals = read_log("interactions"), read_log("eval")
    ids = set(entry.feedback_ids)
    agent = entry.proposed_change.get("agent")
    after_version = entry.prompt_version_after
    applied = _first(interactions, lambda e: e.get("event") == "adaptation_applied"
                     and e.get("adaptation_id") == adaptation_id)

    def uses_new_version(e: dict[str, Any]) -> bool:
        return bool(after_version) and (e.get("prompt_version_set") or {}).get(agent) == after_version

    finders: dict[str, Callable[[], Optional[dict[str, Any]]]] = {
        "E-1": lambda: _first(interactions, lambda e: e.get("event") in ("review_decision", "rating_recorded")
                              and e.get("feedback_id") in ids),
        "E-2": lambda: _first(interactions, lambda e: e.get("event") == "feedback_candidate_created"
                              and e.get("feedback_id") in ids),
        "E-3": lambda: _first(interactions, lambda e: e.get("event") == "feedback_candidate_promoted"
                              and e.get("feedback_id") in ids),
        "E-4": lambda: _first(evals, lambda e: e.get("event") == "eval_result" and e.get("phase") == "before"
                              and e.get("adaptation_id") == adaptation_id),
        "E-5": lambda: _first(interactions, lambda e: e.get("event") == "adaptation_proposed"
                              and e.get("adaptation_id") == adaptation_id),
        "E-6": lambda: _first(interactions, lambda e: e.get("event") == "adaptation_approved"
                              and e.get("adaptation_id") == adaptation_id),
        "E-7": lambda: applied,
        "E-8": lambda: _first(evals, lambda e: e.get("event") == "eval_result" and e.get("phase") == "regression_check"
                              and e.get("adaptation_id") == adaptation_id),
        "E-9": lambda: _first(evals, lambda e: e.get("event") == "pairwise_label_recorded"
                              and e.get("adaptation_id") == adaptation_id),
        "E-10": lambda: _first(interactions, lambda e: e.get("event") == "review_decision" and uses_new_version(e)
                               and (applied is None or e.get("timestamp", "") >= applied.get("timestamp", ""))),
    }
    not_applicable = set()
    if entry.source != "feedback":
        not_applicable |= {"E-2", "E-3"}
    if entry.source == "implicit":
        not_applicable.add("E-1")
    rows = []
    for step, meaning, log in STEPS:
        found = None if step in not_applicable else finders[step]()
        status = "n/a" if step in not_applicable else ("found" if found else "MISSING")
        rows.append({"step": step, "meaning": meaning, "log": f"{log}.log", "status": status,
                     "event": (found or {}).get("event"), "timestamp": (found or {}).get("timestamp"),
                     "fields": {k: found[k] for k in KEY_FIELDS if found and k in found}})
    reward = _first(list(reversed(evals)), lambda e: e.get("event") == "reward_computed" and uses_new_version(e))
    gate = _first(interactions, lambda e: e.get("event") in ("adaptation_rejected", "adaptation_reverted")
                  and e.get("adaptation_id") == adaptation_id)
    return {"entry": entry, "rows": rows, "reward": reward, "gate": gate,
            "missing": [r["step"] for r in rows if r["status"] == "MISSING"]}


def _cell(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text.replace("|", "\\|").replace("\n", " ")


def render(report: dict[str, Any]) -> str:
    entry = report["entry"]
    lines = [f"# Evidence report: {entry.adaptation_id}", "",
             f"Status **{entry.status}** · source {entry.source} · change {entry.proposed_change.get('type')} on "
             f"{entry.proposed_change.get('agent')}"
             + (f" ({entry.prompt_version_before} to {entry.prompt_version_after})" if entry.prompt_version_after
                else ""), "", f"> {entry.explanation or ''}", "",
             "## Evidence chain (Req. Section 16.1)", "",
             "| Step | What it proves | Log | Status | Event | Timestamp | Key fields |",
             "|---|---|---|---|---|---|---|"]
    for r in report["rows"]:
        lines.append(f"| {r['step']} | {r['meaning']} | {r['log']} | {r['status']} | {r['event'] or ''} | "
                     f"{r['timestamp'] or ''} | {_cell(r['fields']) if r['fields'] else ''} |")
    lines += ["", "## Before and after (golden set)", "", "| Metric | Before | After |", "|---|---|---|"]
    before = {m.metric_name: m.value for m in entry.before_metrics}
    after = {m.metric_name: m.value for m in entry.after_metrics}
    for name in sorted(set(before) | set(after)):
        lines.append(f"| {name} | {before.get(name, '')} | {after.get(name, '')} |")
    lines += ["", "## Preference and reward", "",
              f"- Held-out win rate: {entry.held_out_win_rate if entry.held_out_win_rate is not None else 'n/a'}",
              f"- Length change: {entry.length_change_pct if entry.length_change_pct is not None else 'n/a'}%",
              f"- Reward after the change: {(report['reward'] or {}).get('reward_score', 'n/a')} "
              f"({(report['reward'] or {}).get('signal_count', 0)} signals)"]
    if report["gate"]:
        lines += ["", f"Gate outcome: `{report['gate']['event']}`: {report['gate'].get('reason', '')}"]
    lines += ["", f"Missing steps: {', '.join(report['missing']) or 'none'}", ""]
    return "\n".join(lines)


def generate(adaptation_id: str, out_dir: Optional[Path] = None) -> tuple[Path, list[str]]:
    report = build(adaptation_id)
    folder = out_dir or config.get_settings().evidence_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{adaptation_id}.md"
    path.write_text(render(report), encoding="utf-8")
    log_eval("evidence_report_generated", component="evidence_report", adaptation_id=adaptation_id,
             missing_steps=report["missing"], missing_count=len(report["missing"]), path=path.name)
    return path, report["missing"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Write docs/evidence/<adaptation_id>.md from the logs")
    parser.add_argument("--adaptation-id", required=True)
    args = parser.parse_args()
    from src.logger_setup import configure_logging

    configure_logging()
    path, missing = generate(args.adaptation_id)
    print(f"{path}: {'complete' if not missing else 'MISSING ' + ', '.join(missing)}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())

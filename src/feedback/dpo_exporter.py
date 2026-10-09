"""DPO export of agreed pairwise preferences (Architecture Spec Section 10.7; Req. FR-63).

One JSON line per agreed pair with a winner: `prompt` (the agent's input: its
system prompt and the redacted incident), `chosen` and `rejected` (the agent's
part of the two outputs, as JSON text) and `metadata`. Ties, disputed and
SME-decided pairs are left out. Every line is PII-checked. A fine-tune on the
file is a stretch goal (Req. Section 4.4).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

from src import config
from src.logger_setup import log_eval
from src.safety.redaction import redact_record
from src.services import incident_state_store as store

from . import pairwise_session, preference_store


def _incident_text(run_id: str) -> str:
    incident = store.load_incident(run_id) or {}
    keep = ("scenario_id", "input_mode", "raw_input", "reported_service", "reference_time", "window_start",
            "window_end", "hints", "withheld_sources")
    return json.dumps({k: incident.get(k) for k in keep}, ensure_ascii=False, default=str)


def export(today: Optional[date] = None) -> tuple[Path, int]:
    from src.agent import prompts

    folder = config.get_settings().feedback_dir / "dpo_export"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"dpo_{(today or date.today()).isoformat()}.jsonl"
    lines: list[dict[str, Any]] = []
    for session in pairwise_session.sessions():
        agent = session["agent"]
        for p in session["pairs"]:
            status, final = preference_store.pair_outcome(p["pair_id"])
            if status != "agreed" or final not in ("A", "B"):
                continue
            loser = "B" if final == "A" else "A"
            chosen_run, rejected_run = p[f"run_{final.lower()}"], p[f"run_{loser.lower()}"]
            chosen_version = session[f"version_{final.lower()}"]
            line = {
                "prompt": {"system": prompts.render(agent, chosen_version),
                           "input": _incident_text(chosen_run["run_id"])},
                "chosen": json.dumps(pairwise_session.agent_part(store.load_output(chosen_run["run_id"]), agent),
                                     ensure_ascii=False, default=str),
                "rejected": json.dumps(pairwise_session.agent_part(store.load_output(rejected_run["run_id"]),
                                                                   agent), ensure_ascii=False, default=str),
                "metadata": {"pair_id": p["pair_id"], "session_id": session["session_id"], "agent": agent,
                             "scenario_id": p["scenario_id"], "chosen_version": f"{agent}:{chosen_version}",
                             "rejected_version": f"{agent}:{session[f'version_{loser.lower()}']}"},
            }
            clean, _ = redact_record(line)
            lines.append(clean)
    path.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
    log_eval("dpo_exported", component="dpo_exporter", path=str(path.name), count=len(lines))
    return path, len(lines)

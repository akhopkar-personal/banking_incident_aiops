"""Run one investigation through the full LangGraph workflow (LLM agents included).

    python scripts/run_investigation.py SC-01                       # detection replay
    python scripts/run_investigation.py SC-05 --text "Customers charged twice since 10:20 UTC"
    python scripts/run_investigation.py SC-01 --evaluation          # decide, but dispatch nothing
    python scripts/run_investigation.py SC-01 --withhold DEP --full # missing-source variant, full JSON output

Live runs write to data/outbox/ and data/incident_state/, and make LLM calls (about $0.01 per run).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.agent.core_agent import IntakeRejection, run_investigation
    from src.config import SCENARIO_DIRS, get_settings
    from src.schemas.enums import RunMode
    from src.tools.tool_registry import get_registry

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario_id", choices=sorted(SCENARIO_DIRS))
    parser.add_argument("--text", default="", help="alert JSON or free text; omit for a detection replay")
    parser.add_argument("--evaluation", action="store_true", help="evaluation run mode: nothing is dispatched")
    parser.add_argument("--withhold", action="append", default=[], help="evidence prefix to withhold, e.g. DEP")
    parser.add_argument("--full", action="store_true", help="print the whole output")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # citations contain "§"; Windows consoles default to cp1252
    get_settings().apply_to_environment()
    started = time.perf_counter()
    try:
        out = run_investigation(args.text, scenario_id=args.scenario_id, withheld_sources=args.withhold,
                                run_mode=RunMode.EVALUATION if args.evaluation else RunMode.LIVE,
                                progress_callback=lambda node, _: print(f"  . {node}", file=sys.stderr))
    finally:
        get_registry().close()
    if isinstance(out, IntakeRejection):
        print(json.dumps(out.model_dump(), indent=2))
        return 1
    data = out.to_output_dict()
    if not args.full:
        data = {
            "incident_id": data["incident_id"], "status": data["status"], "incident_state": data["incident_state"],
            "issue_class": data.get("issue_class"), "severity": (data.get("severity") or {}).get("level"),
            "rules_fired": [r["rule_id"] for r in (data.get("severity") or {}).get("rules_fired", [])],
            "hypotheses": [f"{h['rank']}. ({h['confidence']}) {h['root_cause']} {h['supporting_evidence']}"
                           for h in data.get("hypotheses", [])],
            "change_findings": [f"{c['change_id']} linked to {c.get('linked_hypothesis_rank')}: {c['rationale']}"
                                for c in data.get("change_findings", [])],
            "actions": [f"{a['step']}. [{a['risk_level']}] {a['action_type']}: {a['action']} ({a['runbook_citation']})"
                        for a in data.get("recommended_actions", [])],
            "needs_human_rca": data.get("needs_human_rca"), "stakeholder_summary": data.get("stakeholder_summary"),
            "regulatory_notes": data.get("regulatory_notes"),
            "insufficient_evidence_reason": data.get("insufficient_evidence_reason"),
            "error_detail": data.get("error_detail"),
            "dispatch": {k: v for k, v in data["dispatch"].items() if k != "notifications"},
            "notifications": len(data["dispatch"].get("notifications", [])),
            "guardrail_flags": data["metadata"]["guardrail_flags"],
            "tokens": data["metadata"]["token_counts"], "cost_usd": data["metadata"].get("cost_estimate_usd"),
            "langfuse_trace_id": data["metadata"].get("langfuse_trace_id"),
            "seconds": round(time.perf_counter() - started, 1),
        }
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

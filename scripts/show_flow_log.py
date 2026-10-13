"""Print the flow log of one investigation run: every log line with its run_id, in time order,
grouped into the five stages (the same log as the Flow log tab of the Incident page).

    python scripts/show_flow_log.py RUN-179172666680503
    python scripts/show_flow_log.py --latest            # the most recent run in the logs
    python scripts/show_flow_log.py RUN-... --jsonl     # the raw JSON lines
    python scripts/show_flow_log.py RUN-... --out run.log
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.logger_setup import read_log
    from src.services import flow_log

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id", nargs="?", help="the run, for example RUN-179172666680503")
    parser.add_argument("--latest", action="store_true", help="the most recent investigation run in the logs")
    parser.add_argument("--jsonl", action="store_true", help="print the raw JSON lines instead")
    parser.add_argument("--out", type=Path, help="write to this file instead of the terminal")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    run_id = args.run_id
    if args.latest:
        finished = [line for line in read_log("interactions")
                    if line.get("event") == "node_completed" and line.get("component") == "finalize"]
        run_id = finished[-1]["run_id"] if finished else None
    if not run_id:
        parser.error("give a run ID or --latest")
    entries = flow_log.load(run_id)
    if not entries:
        print(f"No log lines for {run_id}.", file=sys.stderr)
        return 1
    text = flow_log.to_jsonl(entries) if args.jsonl else flow_log.to_text(entries)
    if not args.jsonl:
        totals = flow_log.summary(entries)
        text += (f"\n{totals['lines']} lines · run {totals['run_s']} s · {totals['llm_calls']} LLM calls, "
                 f"{totals['tokens']} tokens · {totals['tool_calls']} tool calls · {totals['errors']} errors\n")
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

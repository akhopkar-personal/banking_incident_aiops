"""Run a scenario through the rules-only workflow (no LLM): detection, dedup,
severity rules, fast-path page, ticket, and page or assignment.

    python scripts/run_rules_only.py SC-01
    python scripts/run_rules_only.py SC-02 --evaluation      # decide, but dispatch nothing
    python scripts/run_rules_only.py SC-01 --withhold DEP    # golden missing-source variant

Live runs write to data/outbox/ and data/incident_state/. Running the same
scenario twice is deduplicated: the second run creates no incident, ticket or page.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.config import SCENARIO_DIRS
    from src.schemas.enums import RunMode
    from src.services.rules_only_runner import run_rules_only
    from src.tools.tool_registry import get_registry

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario_id", choices=sorted(SCENARIO_DIRS))
    parser.add_argument("--evaluation", action="store_true", help="evaluation run mode: nothing is dispatched")
    parser.add_argument("--withhold", action="append", default=[], help="evidence prefix to withhold, e.g. DEP")
    args = parser.parse_args()
    try:
        result = run_rules_only(args.scenario_id, run_mode=RunMode.EVALUATION if args.evaluation else RunMode.LIVE,
                                withheld_sources=args.withhold)
        print(json.dumps(result.summary(), indent=2))
    finally:
        get_registry().close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Run the golden-set evaluation or the fixture checks (Architecture Spec Section 11.1).

    python scripts/run_eval.py                      # every golden case, DeepEval judge on
    python scripts/run_eval.py --no-judge           # programmatic metrics only (agent cost only)
    python scripts/run_eval.py --scenario SC-03     # one scenario's cases
    python scripts/run_eval.py --cases GC-SC01-REPLAY GC-SC04-REPLAY
    python scripts/run_eval.py --fixtures           # fixture outcomes and S1 fast-path latency

Golden cases run in evaluation mode: nothing is dispatched and the live incident store is
untouched. Results go to data/evaluation/<eval_id>.json and logs/eval.log, and scores to
each run's LangFuse trace. A full run with the judge takes about 12 minutes and $0.15 to $0.25.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.config import get_settings
    from src.evaluation import deepeval_harness, golden_dataset
    from src.logger_setup import configure_logging
    from src.tools.tool_registry import get_registry

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-judge", action="store_true", help="skip the DeepEval metrics")
    parser.add_argument("--scenario", help="only this scenario's cases, for example SC-03")
    parser.add_argument("--cases", nargs="*", help="only these case IDs")
    parser.add_argument("--fixtures", action="store_true", help="run the fixture checks instead")
    parser.add_argument("--concurrency", type=int, default=None, help="runs at a time (default 4)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    get_settings().apply_to_environment()
    configure_logging()

    def progress(done: int, total: int, name: str) -> None:
        print(f"  {done}/{total} {name}", file=sys.stderr)

    try:
        if args.fixtures:
            result = deepeval_harness.run_fixtures(progress=progress)
            for check in result["checks"]:
                print(f"{'PASS' if check['passed'] else 'FAIL'}  {check['check']}: {check['detail']}")
            print(f"{result['passed']} of {result['total']} passed ({result['eval_id']})")
            return 0 if result["passed"] == result["total"] else 1
        case_ids = args.cases or None
        if args.scenario:
            case_ids = [c["case_id"] for c in golden_dataset.cases() if c["scenario_id"] == args.scenario]
        result = deepeval_harness.run_golden_set(case_ids=case_ids, use_judge=not args.no_judge, progress=progress,
                                                 concurrency=args.concurrency)
    finally:
        get_registry().close()
    agg = result["aggregate"]
    print(json.dumps({"eval_id": result["eval_id"], "golden_dataset_version": result["golden_dataset_version"],
                      "prompt_version_set": result["prompt_version_set"], "use_judge": result["use_judge"],
                      "metrics": {k: v for k, v in agg.items() if v is not None}}, indent=2))
    for name, (target, higher) in deepeval_harness.TARGETS.items():
        value = agg.get(name)
        if value is not None:
            met = value >= target if higher else value <= target
            print(f"{'ok ' if met else 'LOW'} {name:28} {value:6.3f}  target {'>=' if higher else '<='} {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

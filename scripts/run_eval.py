"""Run the golden-set evaluation, a consistency check or the fixture checks (Architecture Spec Section 11.1).

    python scripts/run_eval.py                      # every golden case, DeepEval judge on
    python scripts/run_eval.py --no-judge           # programmatic metrics only (agent cost only)
    python scripts/run_eval.py --scenario SC-03     # one scenario's cases
    python scripts/run_eval.py --cases GC-SC01-REPLAY GC-SC04-REPLAY
    python scripts/run_eval.py --fixtures           # fixture outcomes and S1 fast-path latency

  Prompt strategies (FR-78), on the same fixed test set, then a comparison table:
    python scripts/run_eval.py --case-set prompt-comparison                          # active prompts (v1)
    python scripts/run_eval.py --case-set prompt-comparison --variant p2-checklist
    python scripts/run_eval.py --case-set prompt-comparison --variant p3-examples
    python scripts/compare_evals.py EVAL-... EVAL-... EVAL-...

  Retrieval on and off (FR-75):
    python scripts/run_eval.py --case-set prompt-comparison --no-retrieval

  Consistency (FR-79): every case N times, how often the runs agree:
    python scripts/run_eval.py --case-set prompt-comparison --repeat 3

Golden cases run in evaluation mode: nothing is dispatched and the live incident store is
untouched. Results go to data/evaluation/<eval_id>.json and logs/eval.log, and scores to
each run's LangFuse trace. A full run with the judge takes about 12 minutes and $0.15 to $0.25;
an agent run alone costs about $0.004.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.agent import prompts
    from src.config import get_settings
    from src.evaluation import deepeval_harness, golden_dataset
    from src.logger_setup import configure_logging
    from src.tools.tool_registry import get_registry

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-judge", action="store_true", help="skip the DeepEval metrics")
    parser.add_argument("--scenario", help="only this scenario's cases, for example SC-03")
    parser.add_argument("--cases", nargs="*", help="only these case IDs")
    parser.add_argument("--case-set", choices=sorted(golden_dataset.CASE_SETS), help="a named, fixed set of cases")
    parser.add_argument("--variant", help="run this prompt variant (for example p2-checklist) for every agent that has it")
    parser.add_argument("--versions", nargs="*", default=[], metavar="AGENT=VERSION",
                        help="prompt versions per agent, for example rca_agent=p2-checklist")
    parser.add_argument("--no-retrieval", action="store_true", help="run without the knowledge base (FR-75)")
    parser.add_argument("--repeat", type=int, default=0, help="consistency check: run each case this many times")
    parser.add_argument("--label", help="name of this configuration in comparison tables")
    parser.add_argument("--fixtures", action="store_true", help="run the fixture checks instead")
    parser.add_argument("--concurrency", type=int, default=None, help="runs at a time (default 2)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    get_settings().apply_to_environment()
    configure_logging()

    def progress(done: int, total: int, name: str) -> None:
        print(f"  {done}/{total} {name}", file=sys.stderr)

    override: dict[str, str] = {}
    try:
        if args.variant:
            override.update(prompts.variant_override(args.variant))
        for item in args.versions:
            agent, _, version = item.partition("=")
            prompts.resolve_version(agent, {agent: version})  # fails early on a typo
            override[agent] = version
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    case_ids = args.cases or None
    if args.case_set:
        case_ids = golden_dataset.CASE_SETS[args.case_set]
    if args.scenario:
        case_ids = [c["case_id"] for c in golden_dataset.cases() if c["scenario_id"] == args.scenario]

    try:
        if args.fixtures:
            result = deepeval_harness.run_fixtures(progress=progress)
            for check in result["checks"]:
                print(f"{'PASS' if check['passed'] else 'FAIL'}  {check['check']}: {check['detail']}")
            print(f"{result['passed']} of {result['total']} passed ({result['eval_id']})")
            return 0 if result["passed"] == result["total"] else 1
        if args.repeat:
            result = deepeval_harness.run_consistency(args.repeat, case_ids, override or None,
                                                      retrieval_enabled=not args.no_retrieval, progress=progress,
                                                      concurrency=args.concurrency)
            print_consistency(result)
            return 0
        result = deepeval_harness.run_golden_set(override or None, case_ids=case_ids, use_judge=not args.no_judge,
                                                 progress=progress, concurrency=args.concurrency,
                                                 retrieval_enabled=not args.no_retrieval, label=args.label)
    finally:
        get_registry().close()
    agg = result["aggregate"]
    print(json.dumps({"eval_id": result["eval_id"], "label": result["label"],
                      "golden_dataset_version": result["golden_dataset_version"],
                      "prompt_version_set": result["prompt_version_set"], "use_judge": result["use_judge"],
                      "retrieval_enabled": result["retrieval_enabled"],
                      "metrics": {k: v for k, v in agg.items() if v is not None}}, indent=2))
    for name, (target, higher) in deepeval_harness.TARGETS.items():
        value = agg.get(name)
        if value is not None:
            met = value >= target if higher else value <= target
            print(f"{'ok ' if met else 'LOW'} {name:28} {value:6.3f}  target {'>=' if higher else '<='} {target}")
    return 0


def print_consistency(result: dict) -> None:
    scores = result["consistency"]
    print(f"Consistency {result['eval_id']} ({result['label']}): {len(result['cases'])} cases x "
          f"{result['repeats']} runs, ${result['cost_usd']:.4f}")
    for name, value in scores["by_field"].items():
        print(f"  {name:22} {value:6.1%} of cases agree across runs")
    print(f"  {'all fields':22} {scores['all_fields']:6.1%}")
    for case in result["cases"]:
        differ = [f for f, same in case["agrees"].items() if not same]
        print(f"  {case['case_id']:18} {'consistent' if not differ else 'differs in ' + ', '.join(differ)}")


if __name__ == "__main__":
    sys.exit(main())

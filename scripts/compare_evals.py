"""Compare saved golden-set runs on the same test set: the prompt comparison table (FR-78) and the
retrieval on/off comparison (FR-75). The first run is the reference.

    python scripts/compare_evals.py EVAL-A EVAL-B EVAL-C --title "Prompt strategies"
    python scripts/compare_evals.py EVAL-ON EVAL-OFF --title "With and without retrieval" --out docs/evidence/retrieval.md

Eval IDs are printed by scripts/run_eval.py and listed in data/evaluation/. The report goes to
docs/evidence/comparison_<time>.md unless --out is given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.evaluation import comparison

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("eval_ids", nargs="+", help="two or more saved golden-set eval IDs, reference first")
    parser.add_argument("--title", help="report title")
    parser.add_argument("--out", type=Path, help="output file (Markdown)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if len(args.eval_ids) < 2:
        parser.error("give at least two eval IDs")
    try:
        path = comparison.write(args.eval_ids, args.title, args.out)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

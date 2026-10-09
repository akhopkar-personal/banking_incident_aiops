"""Build the FAISS knowledge-base index from knowledge/raw/ (Architecture Spec Section 8).

    python scripts/build_index.py              # OpenAI embeddings (a few cents at most; cached afterwards)
    python scripts/build_index.py --backend hash   # offline hashing embeddings, for LLM-free development only

The index (knowledge/faiss_index/) is not committed; build it once per environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", choices=["openai", "hash"], help="override EMBEDDING_BACKEND")
    args = parser.parse_args()
    if args.backend:
        os.environ["EMBEDDING_BACKEND"] = args.backend

    from src import config
    from src.tool_retrieval import reindex_job

    config.get_settings().apply_to_environment()
    started = time.perf_counter()
    report = reindex_job.run_reindex(force=True)
    report["seconds"] = round(time.perf_counter() - started, 1)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

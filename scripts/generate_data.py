"""Generate the synthetic data (Architecture Spec Section 16).

    python scripts/generate_data.py                 # scenarios, fixtures and knowledge-base PDFs
    python scripts/generate_data.py --check         # verify the committed files are up to date; writes nothing
    python scripts/generate_data.py --write-golden  # also (re)write data/test_inputs.json and data/eval_rubric.json

Output is deterministic. The golden files are written automatically only when
they do not exist yet; after that they are maintained by the SME and the
feedback loop, so overwriting them needs --write-golden.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from data.generators import generate_all  # noqa: E402
from data.generators.builder import json_lines_array, write_text  # noqa: E402
from data.generators.golden import golden_files  # noqa: E402
from data.generators.pdf_writer import markdown_to_pdf  # noqa: E402

PDF_SOURCES = REPO_ROOT / "data" / "generators" / "pdf_sources"
# PDF source (Markdown) -> target under knowledge/raw/
PDF_TARGETS = {
    "PM-2025-019_batch_job_db_contention.md": "postmortems/PM-2025-019_batch_job_db_contention.pdf",
    "REG-001_incident_reporting_obligations.md": "regulatory/REG-001_incident_reporting_obligations.pdf",
}


def generate(root: Path, write_golden: bool) -> list[Path]:
    """Write everything under `root` (a repository-shaped directory); return the paths written."""
    telemetry = root / "data" / "telemetry"
    builders = generate_all(telemetry)
    written = [p for p in telemetry.rglob("*") if p.is_file()]

    for source, target in PDF_TARGETS.items():
        path = root / "knowledge" / "raw" / target
        markdown_to_pdf(PDF_SOURCES / source, path)
        written.append(path)

    inputs_path, rubric_path = root / "data" / "test_inputs.json", root / "data" / "eval_rubric.json"
    if write_golden or not (inputs_path.exists() and rubric_path.exists()):
        inputs, rubric = golden_files(builders)
        write_text(inputs_path, json_lines_array(inputs))
        write_text(rubric_path, json.dumps(rubric, indent=2) + "\n")
        written += [inputs_path, rubric_path]
    return written


def check() -> int:
    """Regenerate into a temporary directory and compare with the repository."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        written = generate(tmp_root, write_golden=False)
        stale = []
        for path in written:
            rel = path.relative_to(tmp_root)
            committed = REPO_ROOT / rel
            if not committed.exists() or not filecmp.cmp(path, committed, shallow=False):
                stale.append(str(rel))
        committed_telemetry = {p.relative_to(REPO_ROOT) for p in (REPO_ROOT / "data" / "telemetry").rglob("*")
                               if p.is_file()}
        generated_telemetry = {p.relative_to(tmp_root) for p in written if "telemetry" in p.parts}
        stale += [f"{p} (not produced by the generator)" for p in sorted(committed_telemetry - generated_telemetry)]
    for item in stale:
        print(f"STALE {item}")
    print("up to date" if not stale else f"{len(stale)} file(s) differ; run python scripts/generate_data.py")
    return 1 if stale else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare with the committed files; write nothing")
    parser.add_argument("--write-golden", action="store_true", help="overwrite the golden dataset seed files")
    args = parser.parse_args()
    if args.check:
        return check()
    written = generate(REPO_ROOT, args.write_golden)
    print(f"wrote {len(written)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Check that no personal data from the source telemetry reached the logs (Scenario 2: "must not
store PII in logs"). No LLM, no cost.

    python scripts/check_logs_pii.py

It collects every personal value in the synthetic telemetry (customer names, emails, phone numbers,
account and card numbers, national IDs, IP and street addresses, user principals), using the same
patterns the redactor uses, and searches the three log files (rotated files included) for each exact
value. Expected: found in the source, 0 found in the logs. Exit code 1 if any value is found.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from src.config import get_settings
    from src.safety import redaction
    from src.safety.pii_names import SYNTHETIC_NAMES

    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    values: dict[str, str] = {}
    for path in (settings.data_dir / "telemetry").rglob("*"):
        if not path.is_file() or path.suffix not in (".json", ".jsonl", ".log", ".csv", ".txt"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for kind, pattern, _ in redaction._PII_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0).strip()
                if kind == "pii_account" and len(set(value)) < 3:
                    continue  # runs such as 00000000 are not personal data
                values.setdefault(value, kind)
        for name in SYNTHETIC_NAMES:
            if name in text:
                values.setdefault(name, "pii_name")
    logs = [p for p in settings.logs_dir.glob("*.log*") if p.is_file()]
    log_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in logs)
    found = {v: k for v, k in values.items() if v in log_text}
    by_kind = Counter(values.values())
    print(f"Personal values in the source telemetry: {len(values)} "
          + "(" + ", ".join(f"{k.replace('pii_', '')} {n}" for k, n in sorted(by_kind.items())) + ")")
    print(f"Log files checked: {', '.join(sorted(p.name for p in logs))} "
          f"({sum(1 for _ in log_text.splitlines())} lines)")
    print(f"Personal values found in the logs: {len(found)}")
    for value, kind in sorted(found.items()):
        print(f"  {kind}: {value[:4]}… (masked)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

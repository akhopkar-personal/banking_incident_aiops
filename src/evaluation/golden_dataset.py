"""Golden dataset: data/eval_rubric.json (ground truth) and data/test_inputs.json
(inputs), one case per input in the same order (Req. Section 12.1).

The 25-case seed comes from scripts/generate_data.py. After that the files grow
only through `feedback_loop.promote`, which appends one case and bumps the
version (golden-v1, golden-v2, ...).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from src import config

RUBRIC, INPUTS = "eval_rubric.json", "test_inputs.json"
_lock = threading.RLock()


def _path(name: str) -> Path:
    return config.get_settings().golden_dir / name


def _read(name: str) -> Any:
    return json.loads(_path(name).read_text(encoding="utf-8"))


def rubric() -> dict[str, Any]:
    with _lock:
        return _read(RUBRIC)


def version() -> str:
    return rubric()["version"]


def cases(case_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """Golden cases, each with its test input under "input", in file order."""
    with _lock:
        data, inputs = _read(RUBRIC), {i["input_id"]: i for i in _read(INPUTS)}
    selected = [c for c in data["cases"] if case_ids is None or c["case_id"] in case_ids]
    return [{**c, "input": inputs[c["input_id"]]} for c in selected]


def case(case_id: str) -> dict[str, Any]:
    found = cases([case_id])
    if not found:
        raise ValueError(f"unknown golden case {case_id}")
    return found[0]


def _bump(current: str) -> str:
    return f"golden-v{int(current.rsplit('v', 1)[1]) + 1}"


def next_case_id(scenario_id: str) -> str:
    code = scenario_id.replace("-", "")
    used = {c["case_id"] for c in rubric()["cases"]}
    n = 1
    while f"GC-{code}-FB{n:03d}" in used:
        n += 1
    return f"GC-{code}-FB{n:03d}"


def add_case(new_case: dict[str, Any], test_input: dict[str, Any]) -> str:
    """Append one case and its input, bump the version, and return the new version."""
    with _lock:
        data, inputs = _read(RUBRIC), _read(INPUTS)
        if any(c["case_id"] == new_case["case_id"] for c in data["cases"]):
            raise ValueError(f"golden case {new_case['case_id']} already exists")
        new_version = _bump(data["version"])
        data["version"] = new_version
        data["cases"].append({**new_case, "version_added": new_version})
        inputs.append(test_input)
        for name, content in ((INPUTS, inputs), (RUBRIC, data)):
            path = _path(name)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(content, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(path)
        return new_version

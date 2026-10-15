"""Compare saved evaluation runs on the same test set (Req. FR-78, FR-75).

    python scripts/compare_evals.py EVAL-A EVAL-B [EVAL-C] [--title ...] [--out FILE]

Writes a Markdown report: the configurations, the metrics side by side with the
change against the first run, and for every case what each configuration
answered and what improved or worsened against the first. The first run is the
reference (normally the baseline prompt, or retrieval on). Outputs are read
back from each run's saved output, so the table shows what the agents said.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src import config
from src.agent import prompts
from src.services import incident_state_store as store

from . import deepeval_harness

# Metric, friendly name, higher is better. The order is the order of the table.
METRICS: list[tuple[str, str, bool]] = [
    ("rca_top1", "Root cause correct (top 1)", True),
    ("rca_top3", "Root cause in top 3", True),
    ("red_herring_rejected", "Red herrings rejected", True),
    ("change_hit", "Causal change found", True),
    ("triage_class_match", "Issue class correct", True),
    ("severity_match", "Severity correct", True),
    ("abstention_correct", "Abstained when it should", True),
    ("citation_validity", "Citations valid", True),
    ("retrieval_recall_at_5", "Right runbook retrieved (top 5)", True),
    ("faithfulness", "Faithfulness (judge)", True),
    ("hallucination_rate", "Hallucination rate (judge)", False),
    ("dispatch_routing_correct", "Dispatch routed correctly", True),
    ("latency_total_s", "Latency per case (s)", False),
    ("cost_usd", "Cost per case (USD)", False),
]
# Per-case metric names (the case results keep the judge's "hallucinated" and the rubric fallbacks).
_CASE_KEYS = {"rca_top1": ("rca_top1", "rca_rubric_top1"), "rca_top3": ("rca_top3", "rca_rubric_top3"),
              "hallucination_rate": ("hallucinated",)}
_QUALITY = [m for m in METRICS if m[0] not in ("latency_total_s", "cost_usd")]


def _case_value(case: dict[str, Any], metric: str) -> Optional[float]:
    for key in _CASE_KEYS.get(metric, (metric,)):
        value = (case.get("metrics") or {}).get(key)
        if value is not None:
            return value
    return None


def _fmt(value: Optional[float], metric: str) -> str:
    if value is None:
        return "n/a"
    if metric == "cost_usd":
        return f"${value:.4f}"
    if metric == "latency_total_s":
        return f"{value:.1f}"
    return f"{value:.2f}"


def _delta(value: Optional[float], base: Optional[float], higher: bool, metric: str) -> str:
    if value is None or base is None or abs(value - base) < 1e-9:
        return ""
    better = (value > base) == higher
    sign = "+" if value > base else "-"
    amount = _fmt(abs(value - base), metric).lstrip("$")
    return f" ({sign}{amount} {'better' if better else 'worse'})"


def _prompt_text(result: dict[str, Any]) -> str:
    override = result.get("version_override") or {}
    if not override:
        return "Active prompts: " + ", ".join(sorted(set((result.get("prompt_version_set") or {}).values())))
    parts = []
    for agent, version in sorted(override.items()):
        parts.append(f"{agent} {version}")
    strategies = {prompts.describe_version(a, v) for a, v in override.items()} - {""}
    return "; ".join(parts) + (f". {' '.join(sorted(strategies))}" if strategies else "")


def _output_summary(case: dict[str, Any], red_herrings: set[str]) -> str:
    if not case.get("run_id"):
        return f"{case.get('status')} ({case.get('error', 'no output')})"
    output = store.load_output(case["run_id"]) or {}
    severity = (output.get("severity") or {}).get("level") or (output.get("rules_severity") or {}).get("level")
    parts = [f"{case.get('status')}", f"severity {severity}", f"class {output.get('issue_class')}"]
    hypotheses = output.get("hypotheses") or []
    if hypotheses:
        cause = hypotheses[0]["root_cause"]
        parts.append(f"top cause ({hypotheses[0]['confidence']:.2f}): {cause[:160]}{'…' if len(cause) > 160 else ''}")
    linked = [f["change_id"] for f in output.get("change_findings") or [] if f.get("linked_hypothesis_rank")]
    if linked:
        parts.append("changes blamed: " + ", ".join(f"{c} (red herring)" if c in red_herrings else c for c in linked))
    else:
        parts.append("no change blamed")
    return " · ".join(parts).replace("|", "/")


def _case_changes(case: dict[str, Any], base: Optional[dict[str, Any]]) -> str:
    if base is None:
        return "reference"
    better, worse = [], []
    for metric, name, higher in _QUALITY:
        value, ref = _case_value(case, metric), _case_value(base, metric)
        if value is None or ref is None or abs(value - ref) < 1e-9:
            continue
        (better if (value > ref) == higher else worse).append(name.lower())
    if not better and not worse:
        return "no change"
    text = []
    if better:
        text.append("improved: " + ", ".join(better))
    if worse:
        text.append("worsened: " + ", ".join(worse))
    return "; ".join(text)


def compare(eval_ids: list[str], title: Optional[str] = None) -> str:
    """The Markdown comparison of the saved golden-set results `eval_ids` (the first is the reference)."""
    results = []
    for eval_id in eval_ids:
        result = deepeval_harness.load_result(eval_id)
        if result is None or result.get("kind") != "golden":
            raise ValueError(f"{eval_id} is not a saved golden-set result")
        results.append(result)
    case_lists = [[c["case_id"] for c in r["cases"]] for r in results]
    same_set = all(set(c) == set(case_lists[0]) for c in case_lists)
    names = [chr(ord("A") + i) for i in range(len(results))]
    lines = [f"# {title or 'Evaluation comparison'}", "",
             f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from saved results in "
             f"`data/evaluation/`. Reference: run A.", ""]
    lines.append(f"**Test set ({len(case_lists[0])} cases{', identical in every run' if same_set else ''}):** "
                 + ", ".join(case_lists[0]))
    if not same_set:
        lines.append("")
        lines.append("> **Warning:** the runs used different cases, so they are not directly comparable.")
    lines += ["", "## Configurations", "", "| Run | Label | Prompt | Retrieval | Judge | Eval ID | Total cost |",
              "|---|---|---|---|---|---|---|"]
    for name, r in zip(names, results):
        cost = sum((c.get("metrics") or {}).get("cost_usd") or 0 for c in r["cases"])
        lines.append(f"| {name} | {r.get('label') or ''} | {_prompt_text(r)} | "
                     f"{'on' if r.get('retrieval_enabled', True) else 'off'} | {'on' if r.get('use_judge') else 'off'} | "
                     f"{r['eval_id']} | ${cost:.4f} |")
    lines += ["", "## Metrics", "", "| Metric | Target | " + " | ".join(names) + " |",
              "|---|---|" + "---|" * len(names)]
    base = results[0]["aggregate"]
    for metric, label, higher in METRICS:
        values = [r["aggregate"].get(metric) for r in results]
        if all(v is None for v in values):
            continue
        target = deepeval_harness.TARGETS.get(metric)
        target_text = f"{'≥' if target[1] else '≤'} {target[0]}" if target else ""
        cells = [_fmt(values[0], metric)] + [_fmt(v, metric) + _delta(v, base.get(metric), higher, metric)
                                             for v in values[1:]]
        lines.append(f"| {label} | {target_text} | " + " | ".join(cells) + " |")
    lines += ["", "## Prompt → Output → What improved or worsened, per case", ""]
    by_case = [{c["case_id"]: c for c in r["cases"]} for r in results]
    for case_id in case_lists[0]:
        scenario = by_case[0][case_id]["scenario_id"]
        red_herrings = set(deepeval_harness._manifest(scenario).get("red_herring_ids") or [])
        lines += [f"### {case_id}", "", "| Run | Output | What improved or worsened (vs A) |", "|---|---|---|"]
        reference = by_case[0].get(case_id)
        for i, (name, cases) in enumerate(zip(names, by_case)):
            case = cases.get(case_id)
            if case is None:
                lines.append(f"| {name} | not run | |")
                continue
            lines.append(f"| {name} | {_output_summary(case, red_herrings)} | "
                         f"{_case_changes(case, None if i == 0 else reference)} |")
        lines.append("")
    lines += ["## Summary against A", ""]
    for name, r in zip(names[1:], results[1:]):
        better = [label for m, label, h in _QUALITY if _delta(r["aggregate"].get(m), base.get(m), h, m).endswith("better)")]
        worse = [label for m, label, h in _QUALITY if _delta(r["aggregate"].get(m), base.get(m), h, m).endswith("worse)")]
        lines.append(f"- **{name} ({r.get('label')})**: improved {', '.join(better) or 'nothing'}; "
                     f"worsened {', '.join(worse) or 'nothing'}.")
    lines += ["", "## Insights and choice of default", "",
              "_To be written after reviewing the outputs above: what changed in the answers, new failure modes, "
              "and which configuration becomes the default and why._", ""]
    return "\n".join(lines)


def write(eval_ids: list[str], title: Optional[str] = None, out: Optional[Path] = None) -> Path:
    path = out or config.get_settings().evidence_dir / f"comparison_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(compare(eval_ids, title), encoding="utf-8")
    return path

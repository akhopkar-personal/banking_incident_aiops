"""Golden-set evaluation (Architecture Spec Section 11.1; Req. Sections 12.1 to 12.4).

    python scripts/run_eval.py                 # golden set, DeepEval metrics on
    python scripts/run_eval.py --no-judge      # programmatic metrics only (cheap)
    python scripts/run_eval.py --fixtures      # fixture outcomes and fast-path latency

Every golden case runs the full graph in `evaluation` mode (nothing dispatched,
its own incident store), optionally with a prompt version override. Each case
gets programmatic checks against the ground truth and, with the judge on, the
DeepEval metrics. Results are saved to data/evaluation/<eval_id>.json, logged
as `eval_result` to eval.log and attached as scores to each run's trace.
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Optional

from src import config
from src.logger_setup import log_error, log_eval
from src.schemas.enums import RunMode
from src.services import incident_state_store as store
from src.tools.dispatch_mocks import outbox
from src.tools.implementations._common import parse_time

from . import golden_dataset, langfuse_tracker

Progress = Callable[[int, int, str], None]
# The 4.x direction change is handled in deepeval_metrics; the notice would repeat for every case.
warnings.filterwarnings("ignore", message=".*HallucinationMetric' now scores", category=DeprecationWarning)

# Metric name -> (target, higher is better). Req. Section 12.2; provisional (OI-8).
TARGETS: dict[str, tuple[float, bool]] = {
    "rca_top1": (0.60, True), "rca_top3": (0.80, True), "retrieval_recall_at_5": (0.85, True),
    "faithfulness": (0.80, True), "hallucination_rate": (0.10, False), "citation_validity": (0.95, True),
    "severity_match": (0.70, True), "triage_class_match": (0.80, True), "change_hit": (0.80, True),
    "abstention_correct": (0.80, True), "dispatch_routing_correct": (1.0, True),
}
# Metrics used for the regression check of an adaptation (Section 11.3).
TRACKED = ("rca_top1", "rca_top3", "retrieval_recall_at_5", "faithfulness", "citation_validity", "severity_match",
           "triage_class_match", "change_hit", "abstention_correct", "red_herring_rejected")
_DOC_ID = re.compile(r"\b(?:RB-[A-Z]{2,4}-\d{3}|PM-\d{4}-\d{3}|REG-\d{3}|SVC-\d{3}|VR-INC-\d{8}-\d{3})\b")

GEVAL_CRITERIA = ("Decide whether the actual output names the same root cause as the expected output: the same "
                  "failing component and the same mechanism. Different wording is fine; a different component, "
                  "a symptom instead of the cause, or an unrelated change is not.")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _manifest(scenario_id: str) -> dict[str, Any]:
    return json.loads((config.get_settings().scenario_dir(scenario_id) / "manifest.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------- programmatic


def _rubric_match(hypothesis: dict[str, Any], case: dict[str, Any]) -> bool:
    """Without a judge: the hypothesis cites the causal change, or at least half of the must-cite evidence."""
    cited = set(hypothesis.get("supporting_evidence") or [])
    if case.get("expected_causal_change_id") and case["expected_causal_change_id"] in cited:
        return True
    must = set(case.get("must_cite_evidence_ids") or [])
    return bool(must) and len(cited & must) >= max(1, len(must) / 2)


def programmatic_checks(output: dict[str, Any], case: dict[str, Any],
                        retrieval: Optional[list[dict[str, Any]]] = None) -> dict[str, Optional[float]]:
    """Checks that need no judge (Section 11.1). None means "not applicable to this case"."""
    status = output.get("status")
    if status == "SYSTEM_ERROR":
        level = (output.get("rules_severity") or {}).get("level")
        return {"system_error": 1.0, "severity_match": float(level == case["expected_severity"])}
    metrics: dict[str, Optional[float]] = {"system_error": 0.0}
    level = (output.get("severity") or {}).get("level")
    metrics["severity_match"] = float(level == case["expected_severity"])
    metrics["severity_within_1"] = float(bool(level) and abs(int(level[1]) - int(case["expected_severity"][1])) <= 1)
    metrics["triage_class_match"] = float(output.get("issue_class") == case["expected_issue_class"])
    abstained = status == "INSUFFICIENT_EVIDENCE" or bool(output.get("needs_human_rca"))
    metrics["abstention_correct"] = float(abstained == bool(case["expected_abstention"]))

    findings = output.get("change_findings") or []
    causal = case.get("expected_causal_change_id")
    metrics["change_hit"] = (float(any(f["change_id"] == causal and f.get("linked_hypothesis_rank") == 1
                                       for f in findings)) if causal else None)
    red_herrings = set(_manifest(case["scenario_id"]).get("red_herring_ids") or [])
    linked = {f["change_id"] for f in findings if f.get("linked_hypothesis_rank")}
    metrics["red_herring_rejected"] = float(not (linked & red_herrings)) if red_herrings else None

    hypotheses = output.get("hypotheses") or []
    if case["expected_abstention"]:
        metrics["rca_rubric_top1"] = metrics["rca_rubric_top3"] = None
    else:
        metrics["rca_rubric_top1"] = float(bool(hypotheses) and _rubric_match(hypotheses[0], case))
        metrics["rca_rubric_top3"] = float(any(_rubric_match(h, case) for h in hypotheses[:3]))

    evidence = output.get("evidence") or {}
    cited = [e for h in hypotheses for e in h.get("supporting_evidence") or []]
    retrieved_ids = [d["doc_id"] for d in retrieval or []]
    cited_docs = [d for a in output.get("recommended_actions") or [] for d in _DOC_ID.findall(a["runbook_citation"])]
    total = len(cited) + len(cited_docs)
    valid = sum(e in evidence for e in cited) + sum(d in retrieved_ids for d in cited_docs)
    metrics["citation_validity"] = valid / total if total else 1.0
    top5 = list(dict.fromkeys(retrieved_ids))[:5]
    metrics["retrieval_recall_at_5"] = (None if case["expected_abstention"] and not top5
                                        else float(case["expected_runbook_id"] in top5))

    intended = (output.get("dispatch") or {}).get("intended") or {}
    expected = case.get("expected_dispatch") or {}
    if expected.get("route") == "page":
        routed = "page" in intended and (not expected.get("fast_path") or bool(intended["page"].get("fast_path")))
    else:
        routed = "itsm_assign" in intended and "page" not in intended
    metrics["dispatch_routing_correct"] = float(routed)
    return metrics


# ----------------------------------------------------------------- DeepEval


def _texts(output: dict[str, Any], retrieval: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The answer to judge, and the context it should be grounded in."""
    hypotheses = output.get("hypotheses") or []
    answer = "\n".join(filter(None, [
        f"Root cause: {hypotheses[0]['root_cause']}" if hypotheses else "",
        "Actions: " + "; ".join(a["action"] for a in output.get("recommended_actions") or []),
        output.get("stakeholder_summary") or "",
    ]))
    context = [f"{eid}: {item['summary']}" for eid, item in (output.get("evidence") or {}).items()]
    context += [f"{d['doc_id']} {d.get('section') or ''}: {d.get('excerpt', '')}" for d in retrieval[:5]]
    return answer, context or ["(no evidence)"]


def _cited_context(output: dict[str, Any], retrieval: list[dict[str, Any]]) -> list[str]:
    evidence = output.get("evidence") or {}
    hypotheses = output.get("hypotheses") or []
    cited = hypotheses[0].get("supporting_evidence") or [] if hypotheses else []
    docs = {d for a in output.get("recommended_actions") or [] for d in _DOC_ID.findall(a["runbook_citation"])}
    context = [f"{e}: {evidence[e]['summary']}" for e in cited if e in evidence]
    context += [f"{d['doc_id']} {d.get('section') or ''}: {d.get('excerpt', '')}" for d in retrieval
                if d["doc_id"] in docs][:3]
    return context or ["(nothing cited)"]


def deepeval_metrics(output: dict[str, Any], case: dict[str, Any], retrieval: list[dict[str, Any]],
                     judge: Any) -> dict[str, Optional[float]]:
    """GEval (RCA correctness), Faithfulness, Hallucination and Contextual Recall with the given judge."""
    from deepeval.metrics import ContextualRecallMetric, FaithfulnessMetric, GEval, HallucinationMetric
    from deepeval.test_case import LLMTestCase, SingleTurnParams

    out: dict[str, Optional[float]] = {}
    answer, context = _texts(output, retrieval)
    question = f"What is the root cause of incident {output['incident_id']} and what should be done?"

    def measure(name: str, metric: Any, test_case: Any) -> Optional[float]:
        try:
            metric.measure(test_case)
            return float(metric.score)
        except Exception as exc:  # noqa: BLE001 - one failed judge call must not lose the case
            log_error("system_error", component="deepeval_harness", stage=f"metric_{name}", error=type(exc).__name__,
                      incident_id=output.get("incident_id", "unknown"), run_id=output.get("run_id", "unknown"))
            return None

    hypotheses = output.get("hypotheses") or []
    if not case["expected_abstention"]:
        top1 = top3 = None
        for rank, hypothesis in enumerate(hypotheses[:3], start=1):
            geval = GEval(name="RCA correctness", criteria=GEVAL_CRITERIA, model=judge, threshold=0.5,
                          evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
                          async_mode=False)
            score = measure("geval", geval, LLMTestCase(input=question, actual_output=hypothesis["root_cause"],
                                                        expected_output=case["expected_root_cause"]))
            if score is None:
                continue
            hit = score >= 0.5
            if rank == 1:
                top1 = float(hit)
            if hit:
                top3 = 1.0
                break
            top3 = 0.0
        out["rca_top1"] = top1 if hypotheses else 0.0
        out["rca_top3"] = top3 if hypotheses else 0.0
        out["contextual_recall"] = measure("contextual_recall", ContextualRecallMetric(
            model=judge, threshold=0.5, async_mode=False, include_reason=False), LLMTestCase(
            input=question, actual_output=answer, expected_output=case["expected_root_cause"],
            retrieval_context=context))
    out["faithfulness"] = measure("faithfulness", FaithfulnessMetric(
        model=judge, threshold=0.5, async_mode=False, include_reason=False),
        LLMTestCase(input=question, actual_output=answer, retrieval_context=context))
    # Against what the answer cites (top hypothesis evidence, cited documents): unrelated evidence and other
    # runbooks' advice in the context are counted as contradictions by the judge. DeepEval 4.x scores 1 for
    # "consistent with every context item".
    consistent = measure("hallucination", HallucinationMetric(
        model=judge, threshold=0.5, async_mode=False, include_reason=False),
        LLMTestCase(input=question, actual_output=answer, context=_cited_context(output, retrieval)))
    out["hallucination"] = None if consistent is None else round(1 - consistent, 4)  # higher is worse
    out["hallucinated"] = None if consistent is None else float(consistent < 0.5)
    return out


# --------------------------------------------------------------------- runs


def run_case(case: dict[str, Any], version_override: Optional[dict[str, str]] = None, judge: Any = None
             ) -> dict[str, Any]:
    """One golden case through the graph in evaluation mode, with its metrics."""
    from src.agent.core_agent import IntakeRejection, run_investigation

    test_input = case["input"]
    started = time.perf_counter()
    result: dict[str, Any] = {"case_id": case["case_id"], "scenario_id": case["scenario_id"],
                              "variant": case["variant"], "run_id": None, "status": None, "metrics": {}}
    try:
        output = run_investigation(
            test_input["raw_input"], scenario_id=case["scenario_id"], supplied_window=test_input.get("window"),
            run_mode=RunMode.EVALUATION, prompt_version_override=version_override or None,
            reference_time=parse_time(test_input["reference_time"]) if test_input.get("reference_time") else None,
            withheld_sources=test_input.get("withheld_sources") or [])
    except Exception as exc:  # noqa: BLE001 - recorded as a failed case
        log_error("system_error", component="deepeval_harness", exc=exc, incident_id="unknown", run_id="unknown",
                  case_id=case["case_id"])
        result.update(status="HARNESS_ERROR", error=type(exc).__name__)
        return result
    if isinstance(output, IntakeRejection):
        result.update(status="INTAKE_REJECTED", error=output.reason)
        return result
    data = output.to_output_dict()
    retrieval = store.load_retrieval(output.run_id)
    metrics = programmatic_checks(data, case, retrieval)
    if judge is not None and data["status"] != "SYSTEM_ERROR":
        metrics.update(deepeval_metrics(data, case, retrieval, judge))
    meta = data.get("metadata") or {}
    metrics["latency_total_s"] = round(time.perf_counter() - started, 2)
    metrics["cost_usd"] = meta.get("cost_estimate_usd")
    result.update(run_id=output.run_id, status=data["status"], metrics=metrics,
                  langfuse_trace_id=meta.get("langfuse_trace_id"), prompt_version_set=meta.get("prompt_version_set"),
                  latency_ms_per_stage=meta.get("latency_ms_per_stage") or {})
    langfuse_tracker.attach_scores(meta.get("langfuse_trace_id"), {
        k: v for k, v in metrics.items() if k in TARGETS or k in ("hallucination", "latency_total_s", "cost_usd")})
    return result


def aggregate(results: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    """Mean of each metric over the cases where it applies. rca_top1/3 fall back to the rubric without a judge."""
    def values(name: str) -> list[float]:
        return [r["metrics"][name] for r in results if r["metrics"].get(name) is not None]

    agg: dict[str, Optional[float]] = {}
    names = {n for r in results for n in r["metrics"]}
    for name in sorted(names):
        found = values(name)
        agg[name] = round(mean(found), 4) if found else None
    for rank in ("top1", "top3"):
        if agg.get(f"rca_{rank}") is None:
            agg[f"rca_{rank}"] = agg.get(f"rca_rubric_{rank}")
    agg["hallucination_rate"] = agg.pop("hallucinated", None)
    agg["cases"] = float(len(results))
    agg["failed_cases"] = float(sum(r["status"] in (None, "HARNESS_ERROR", "INTAKE_REJECTED") for r in results))
    return agg


def _save(result: dict[str, Any]) -> Path:
    folder = config.get_settings().evaluation_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result['eval_id']}.json"
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return path


def new_eval_id(prefix: str = "EVAL") -> str:
    return f"{prefix}-{now():%Y%m%dT%H%M%S%f}"[:-3]


def run_golden_set(version_override: Optional[dict[str, str]] = None, *, phase: str = "baseline",
                   adaptation_id: Optional[str] = None, case_ids: Optional[list[str]] = None,
                   use_judge: bool = True, judge: Any = None, progress: Optional[Progress] = None,
                   concurrency: Optional[int] = None) -> dict[str, Any]:
    """Run the golden set (or `case_ids`) and return the saved result (Section 11.1)."""
    from src.agent import prompts

    cases = golden_dataset.cases(case_ids)
    if not cases:
        raise ValueError("no golden cases selected")
    if use_judge and judge is None:
        from .judge import FunctionCallingJudge

        judge = FunctionCallingJudge()
    version_set = {**prompts.active_version_set(), **(version_override or {})}
    eval_id = new_eval_id()
    started = now()
    results: list[dict[str, Any]] = []
    workers = concurrency or config.get_settings().eval_concurrency
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="eval") as pool:
        futures = {pool.submit(run_case, c, version_override, judge if use_judge else None): c for c in cases}
        for future in as_completed(futures):
            results.append(future.result())
            if progress:
                progress(len(results), len(cases), futures[future]["case_id"])
    order = {c["case_id"]: i for i, c in enumerate(cases)}
    results.sort(key=lambda r: order[r["case_id"]])
    result = {"eval_id": eval_id, "kind": "golden", "phase": phase, "adaptation_id": adaptation_id,
              "golden_dataset_version": golden_dataset.version(), "prompt_version_set": version_set,
              "version_override": version_override or {}, "use_judge": bool(use_judge),
              "started_at": started.isoformat(), "finished_at": now().isoformat(),
              "aggregate": aggregate(results), "cases": results}
    _save(result)
    log_eval("eval_result", component="deepeval_harness", eval_id=eval_id, phase=phase, adaptation_id=adaptation_id,
             golden_dataset_version=result["golden_dataset_version"], prompt_version_set=version_set,
             metrics={k: v for k, v in result["aggregate"].items() if v is not None}, cases=len(results),
             use_judge=bool(use_judge))
    langfuse_tracker.sync_golden_dataset(golden_dataset.cases(), result["golden_dataset_version"])
    return result


# ----------------------------------------------------------------- fixtures


def _first_llm_call(run_id: str) -> Optional[datetime]:
    path = config.get_settings().logs_dir / "interactions.log"
    if not path.exists():
        return None
    times = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if run_id in line and '"llm_call"' in line:
            try:
                times.append(datetime.fromisoformat(json.loads(line)["timestamp"]))
            except (ValueError, KeyError):
                continue
    return min(times) if times else None


def _sandbox_run(scenario_id: str, root: Path) -> tuple[Any, dict[str, list[dict[str, Any]]], list[Any]]:
    """A live run whose store and outbox are a temporary sandbox: real dispatch, nothing shared."""
    from src.agent.core_agent import run_investigation

    with store.scoped(root / "state"), outbox.scoped(root / "outbox"):
        output = run_investigation(scenario_id=scenario_id, run_mode=RunMode.LIVE)
        sent = {kind: outbox.read(kind) for kind in outbox.FILES}
        incidents = store.list_incidents()
    return output, sent, incidents


def run_fixtures(progress: Optional[Progress] = None) -> dict[str, Any]:
    """Req. 12.2 fixture outcomes plus the S1 fast-path latency, each run live in a sandbox."""
    checks: list[dict[str, Any]] = []
    started = now()

    def record(name: str, passed: bool, detail: str, **extra: Any) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail, **extra})

    steps = ["FX-FAILURE", "FX-LLM-DOWN", "FX-ALERT-STORM", "FX-INJECTION", "SC-01", "SC-03", "SC-04"]
    with tempfile.TemporaryDirectory(prefix="aiops-fixtures-") as tmp:
        for i, scenario_id in enumerate(steps, start=1):
            root = Path(tmp) / scenario_id
            try:
                output, sent, incidents = _sandbox_run(scenario_id, root)
            except Exception as exc:  # noqa: BLE001
                record(scenario_id, False, f"harness error: {type(exc).__name__}")
                continue
            data = output.to_output_dict() if hasattr(output, "to_output_dict") else {}
            new_pages = [p for p in sent["pages"] if p.get("mode") == "new"]
            if scenario_id == "FX-FAILURE":
                error = data.get("error_detail") or {}
                record("system_error_handling", data.get("status") == "SYSTEM_ERROR"
                       and error.get("retry_count", 99) <= config.get_settings().max_node_retries
                       and "@" not in error.get("message", ""),
                       f"status {data.get('status')}, failed node {error.get('failed_node')}")
            elif scenario_id == "FX-LLM-DOWN":
                record("llm_down_fallback", data.get("status") == "SYSTEM_ERROR" and len(new_pages) == 1
                       and (data.get("rules_severity") or {}).get("level") == "S1" and bool(sent["itsm"]),
                       f"status {data.get('status')}, pages {len(new_pages)}, tickets {len(sent['itsm'])}")
            elif scenario_id == "FX-ALERT-STORM":
                tickets = [t for t in sent["itsm"] if t.get("operation") == "upsert" and t.get("created")]
                record("dedup_correct", len(incidents) == 1 and len(new_pages) == 1 and len(tickets) == 1,
                       f"incidents {len(incidents)}, new pages {len(new_pages)}, tickets created {len(tickets)}")
            elif scenario_id == "FX-INJECTION":
                flags = (data.get("metadata") or {}).get("guardrail_flags") or []
                actions = {a["action_type"] for a in data.get("recommended_actions") or []}
                denied = actions & {"delete_topic", "purge_data"}
                record("guardrail_pass", "injection_suspected" in flags and not denied, f"flags {sorted(set(flags))}")
            else:
                page_time = datetime.fromisoformat(new_pages[0]["at"]) if new_pages else None
                created = incidents[0].created_at if incidents else None
                latency = (page_time - created).total_seconds() if page_time and created else None
                first_llm = _first_llm_call(data.get("run_id", ""))
                before_llm = page_time is not None and (first_llm is None or page_time <= first_llm)
                record(f"critical_page_latency {scenario_id}", latency is not None and latency <= 10 and before_llm,
                       f"page {latency:.1f} s after creation, before the first LLM call: {before_llm}"
                       if latency is not None else "no page", critical_page_latency_s=latency)
            if progress:
                progress(i, len(steps), scenario_id)
    result = {"eval_id": new_eval_id("FIXT"), "kind": "fixtures", "started_at": started.isoformat(),
              "finished_at": now().isoformat(), "checks": checks,
              "passed": sum(c["passed"] for c in checks), "total": len(checks)}
    _save(result)
    log_eval("eval_result", component="deepeval_harness", eval_id=result["eval_id"], phase="fixtures",
             passed=result["passed"], total=result["total"],
             checks={c["check"]: c["passed"] for c in checks})
    return result


# ------------------------------------------------------------------- reading


def saved_results(kind: Optional[str] = None) -> list[dict[str, Any]]:
    """Saved evaluation results, newest first."""
    folder = config.get_settings().evaluation_dir
    if not folder.exists():
        return []
    found = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if kind is None or data.get("kind") == kind:
            found.append(data)
    return sorted(found, key=lambda d: d.get("finished_at") or "", reverse=True)


def load_result(eval_id: str) -> Optional[dict[str, Any]]:
    path = config.get_settings().evaluation_dir / f"{eval_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

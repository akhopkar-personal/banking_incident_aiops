# Evidence map: EventHub AIOps capstone

Which evidence exists for each capstone requirement, and where to find it: the log file, the event to
filter on, and the run, evaluation or file that holds it. Generated after the evidence runs of
15 October 2026 (gpt-4o-mini through the Vocareum gateway, prompts v1 unless stated, code at
commit `b0ea7e9` plus the two fixes listed in Section 10).

## 1. Where the evidence lives

| Location | What it holds | How to read it |
|---|---|---|
| `logs/interactions.log` | Every step of every run as JSON lines: anomalies, rules, tool and LLM calls (tokens, latency), guardrails, gates, dispatch, notifications, refusals, human decisions | Filter by `run_id` and `event`, or `python scripts/show_flow_log.py RUN-…` |
| `logs/error.log` | Retries (`node_retry`), failures (`system_error` with stack trace), failed or blocked tool calls (`tool_call` with `status`, `tool_access_blocked`) | Filter by `run_id`, `event`, `level` |
| `logs/eval.log` | One `eval_result` line per evaluation, fixture or consistency run, with all metrics | Filter by `eval_id` |
| `data/evaluation/*.json` | Full evaluation results: per-case metrics, run IDs, configuration (prompt versions, retrieval, judge) | Open by evaluation ID |
| `data/incident_state/evaluation/<run_id>/` | Saved output and retrieved documents of each evaluation run | `incident_state_store.load_output(run_id)` |
| `data/outbox/` | Simulated pages, ITSM tickets and notifications (live runs) | Outbox page in the UI |
| `docs/evidence/capstone/runs/` | Console output of each evidence run, numbered in the order they ran | Text files |
| `docs/evidence/capstone/flowlogs/` | Readable flow logs (all log lines of one run, by stage) of the key runs | Text files; the same view is in the UI (Incident page, Flow log tab) |
| `docs/evidence/capstone/*.md` | Comparison reports (prompt strategies; with and without retrieval) | Markdown |
| LangFuse (cloud) | One trace per run, with LLM generations and evaluation scores | Search by the `langfuse_trace_id` in the output or log line |

`logs/`, `data/evaluation/` and `data/incident_state/` are not in git. Include them in the submission zip.

## 2. Phase 1: problem and success criteria

| Evidence | Where |
|---|---|
| Persona, workflow, problem, inputs and outputs, constraints, assumptions | `docs/AI_Capstone_Project_Requirements_v0.18.md`, Sections 1 to 6 |
| Success criteria and metric targets | Requirements Section 12.2; targets also in `src/evaluation/deepeval_harness.py` (`TARGETS`) |
| Example user inputs (alert JSON, free text) | `data/test_inputs.json` |
| Known failure cases and edge scenarios | Fixtures FX-FAILURE, FX-LLM-DOWN, FX-ALERT-STORM, FX-INJECTION (`data/telemetry/*_fixture/`); golden variants narrow window, wide window, missing source |

## 3. Phase 2: baseline agent (rules only)

| Evidence | Run or file | Log file and event |
|---|---|---|
| Rules-only baseline on SC-02 and SC-04: severity and page decision only; no issue class, no root cause, no actions | `runs/06_baseline_rules_only_SC-02.txt` (RUN-179207222276501), `runs/06_baseline_rules_only_SC-04.txt` (RUN-179207222639401) | `interactions.log`: `incident_created`, `severity_rules_evaluated` (`pass_name` pass1), `dispatch_suppressed` |
| The same scenario with the LLM agents (for the comparison) | `runs/01_sc04_live_investigation.txt` (RUN-179207013737201) | `interactions.log`: `llm_call`, `tool_call`, `node_completed` for the four agents |

## 4. Phase 3: LLM and prompt strategies

| Evidence | Run or file | Log file and event |
|---|---|---|
| **Prompt comparison table** (same 6 cases; v1, p2-checklist, p3-examples; Prompt → Output → What improved or worsened) | `prompt_comparison.md` | `eval.log`: `eval_result` for EVAL-20261015T132212386 (v1), EVAL-20261015T132550004 (p2), EVAL-20261015T132931844 (p3) |
| Per-run outputs behind the table | `runs/02b_prompt_v1.txt`, `runs/02_prompt_p2-checklist.txt`, `runs/02_prompt_p3-examples.txt`; `data/evaluation/EVAL-…json` | `interactions.log`: `llm_call` per case (`prompt_version_set` shows the variant) |
| New failure modes of the variants: p3 made the RCA return its signal map as a list (SYSTEM_ERROR), and over-abstained (3 of 6 INSUFFICIENT_EVIDENCE); p2 and p3 lowered root-cause accuracy but stopped hallucinations | `prompt_comparison.md` (cases GC-SC01-FREETEXT, GC-SC01-REPLAY, GC-SC02-REPLAY, GC-SC04-REPLAY) | `error.log`: `system_error` for RUN-179207097269502 (`RcaDraft … Input should be a valid dictionary`) |
| Prompt texts of the variants | `data/prompt_versions.json` (`p2-checklist`, `p3-examples`, with a `strategy` line each) | none |

## 5. Phase 4: knowledge and retrieval

| Evidence | Run or file | Log file and event |
|---|---|---|
| Documents prepared, chunked (800 characters, 100 overlap), embedded and indexed | `knowledge/raw/`, `knowledge/faiss_index/index_meta.json` | `eval.log`: `knowledge_index_built` |
| Retrieval connected to answers: actions cite runbook sections (for example RB-NET-004 §4, RB-PAY-003 §4) | `runs/01_sc04_live_investigation.txt`, `flowlogs/sc04_live_full_flow.log` | `interactions.log`: `tool_call` with `tool` search_knowledge_base |
| **With vs without retrieval on the same 6 cases**: right runbook retrieved 1.00 → 0.00, faithfulness 0.92 → 0.87, red herrings rejected 0.83 → 0.67; without retrieval the guardrail removed every uncited fix action, leaving only "investigate" and "inform customers" | `retrieval_comparison.md`, `runs/03_actions_with_vs_without_retrieval.txt`, `runs/03_no_retrieval.txt` | `eval.log`: EVAL-20261015T132212386 vs EVAL-20261015T133336971; `interactions.log`: `tool_call` with `status` retrieval_disabled; `guardrail_block` with `uncited_action_removed` |
| Missing information handled (golden "missing source" variants) | `runs/12_missing_evidence.txt`, `runs/12b_missing_evidence_detail.txt` (EVAL-20261015T144641054) | `eval.log`: `eval_result` (`abstention_correct`). Finding: not handled well, see Section 10 |

## 6. Phase 5: tools

| Evidence | Run or file | Log file and event |
|---|---|---|
| Tools defined with schemas (13 read-only tools over MCP, dispatch mocks) | `src/tools/tool_registry.py`, `src/mcp_server/server.py` | `interactions.log`: `mcp_server_started` (lists the tools) |
| Correct tool selection per agent (for example the RCA Agent calls query_network, query_logs, query_api_metrics for a TLS incident) | `flowlogs/sc04_live_full_flow.log` | `interactions.log`: `tool_call` (`caller`, `tool`, `transport` mcp, `status` ok) for RUN-179207013737201 |
| **A real incorrect tool call by the LLM, corrected by the model**: at +16.2 s the RCA Agent sent four calls with `service` as text instead of a list; each was rejected and the error returned to the model, which repeated them correctly at +19.5 s | `flowlogs/sc04_live_full_flow.log` (RUN-179207013737201) | `interactions.log`: `tool_call` WARNING `status` invalid_arguments, then `tool_call` `status` ok for the same tools |
| **Incorrect or failed tool calls and safeguards, forced** (wrong arguments, a tool outside the agent's set, a dispatch tool asked for by an agent, a duplicate call, calls over the budget of 8) | `runs/07_tool_safeguards_demo.txt`, `flowlogs/tool_safeguards_demo.log` (RUN-DEMO-TOOLS-20261015134820) | `interactions.log`: `tool_call` WARNING with `status` invalid_arguments, duplicate_call, budget_exhausted; `error.log`: `tool_access_blocked` |
| A real tool failure during a run (knowledge-base search timed out 3 times after a network loss) | `flowlogs/sc03_system_error_network_timeouts.log` (RUN-179207236546601) | `error.log`: `tool_call` (`status` failed), `node_retry`, `system_error` |

## 7. Phase 6: planning, memory and context

| Evidence | Run or file | Log file and event |
|---|---|---|
| Multi-step plan: 22-node LangGraph workflow, RCA ∥ Change Correlation in parallel, two-round RCA tool plan | `flowlogs/sc04_live_full_flow.log` (stages Detect → Triage → Analyse → Respond) | `interactions.log`: `node_completed` in order |
| Clarification turn: input without a time is answered with a question; the second turn is investigated | `runs/09_clarification_two_turns.txt` | Turn 1 has no log line (rejected before an incident exists); turn 2: `incident_created`, `llm_call` |
| Short-term memory across runs: a repeat alert updates the same incident and ticket instead of opening new ones | `runs/10_repeat_alert_dedup_sc04.txt`, `flowlogs/sc04_repeat_alert_dedup.log` (RUN-179207230901901, incident INC-20260402-001, ticket MOCK-ITSM-0002) | `interactions.log`: `incident_deduplicated`, then `dispatch_page` with `mode` update (no second new page) |
| Long-term memory: a fix verified on 11 October (VR-INC-20260327-001) is the top retrieved document for a new SC-03 incident | `runs/11b_long_term_memory_retrieval.txt`, `flowlogs/sc03_long_term_memory.log` (RUN-179207569709101) | `interactions.log`: `resolution_indexed` (11 October), `tool_call` search_knowledge_base (15 October) |
| Retention and reset rules | Requirements Section 10.3.3 | none |

## 8. Phase 7: adaptive behaviour

| Evidence | Run or file | Log file and event |
|---|---|---|
| Feedback stored (reviews with ratings, candidates, pairwise labels) | `data/feedback/` | `interactions.log`: `review_decision`, `rating_recorded`, `feedback_candidate_created` |
| E-1, E-2: two reviewers (`rev-a`, `rev-b`) rejected the two SC-05 runs as *root cause error*, with reasons and ratings; each became a feedback candidate (FC-0001, FC-0002) | `flowlogs/adp001_reviewed_run_RUN-179241083651601.log`, `flowlogs/adp001_reviewed_run_RUN-179241164578102.log` (incident INC-20260409-001) | `interactions.log`: `review_decision`, `rating_recorded`, `feedback_candidate_created` |
| E-3: the SME (`sme-1`) promoted both into the golden dataset (golden-v1 → v2 → v3, 27 cases) | `data/eval_rubric.json`, `data/test_inputs.json` (variant `feedback`) | `interactions.log`: `feedback_candidate_promoted` |
| E-5: the scan found the pattern (2 signals, 1 per reviewer, threshold 2) and proposed ADP-001, a prompt rule for the RCA Agent drafted by the LLM from the reviewers' reasons | `ADP-001_evidence_report.md`; `data/feedback/adaptation_log.json` | `interactions.log`: `adaptation_proposed` (ADP-001, ADP-002, ADP-003) |
| **A rejected adaptation**: ADP-002 (low RCA ratings, same problem) rejected by the Evaluation Engineer with a reason | `data/feedback/adaptation_log.json` | `interactions.log`: `adaptation_rejected` (ADP-002, reviewer `ee-1`, reason "Same problem as ADP-001 … one change at a time") |
| E-6: the Evaluation Engineer (`ee-1`) approved ADP-001 | `ADP-001_evidence_report.md` | `interactions.log`: `adaptation_approved` |
| E-4, E-8: **before vs after on the full golden set (27 cases, judge on)**: root cause top-1 0.55 → 0.36 and top-3 0.59 → 0.50; faithfulness +4 and severity +4 points; nothing else changed. Target not improved, so the engine **reverted** v2 and kept v1 | `adp001_before_after.md` (per-case Prompt → Output → What changed), `ADP-001_evidence_report.md` | `eval.log`: `eval_result` phase before (EVAL-20261019T130818577), after (EVAL-20261019T132325374), phase regression_check `fail`; `interactions.log`: `adaptation_reverted` |
| E-7, E-9, E-10: not applicable after a revert (v2 never went live, so there is nothing to compare blind or to use in new runs); the report marks them MISSING | `ADP-001_evidence_report.md` | none, by design |
| Behaviour change by feedback, shown elsewhere: the SME-promoted cases now sit in every evaluation run (golden-v3), and the clarification and memory evidence in Section 7 | Section 7; `data/eval_rubric.json` | `eval.log`: `eval_result` (`golden_dataset_version` golden-v3) |

## 9. Phases 8 and 9: deployment, evaluation and safety

| Evidence | Run or file | Log file and event |
|---|---|---|
| Latency per run and per stage | Flow logs (seconds per step); `data/evaluation/*.json` (`latency_ms_per_stage`) | `interactions.log`: `node_completed` (`latency_ms`), `llm_call` (`latency_ms`) |
| Graceful failure: node failure, LLM down (rules-only page still sent), alert storm (one incident), S1 page in 0.1 to 0.4 s before any LLM call | `runs/05_fixtures.txt` (FIXT-20261015T134752497, 7 of 7 passed) | `eval.log`: `eval_result` phase fixtures; `error.log`: `system_error` |
| Error logs of a real failure | `flowlogs/sc03_system_error_network_timeouts.log` | `error.log`: `node_retry`, `system_error` |
| Full golden-set quality metrics (25 cases, judge on): severity 0.96, issue class 0.96, runbook retrieved 0.96, faithfulness 0.91, hallucination 0.08, citations 1.00, abstention 0.80; below target: root cause top 1 0.25, top 3 0.40, causal change 0.21, dispatch routing 0.96 | `runs/13_full_golden_baseline.txt` (EVAL-20261015T144951477) | `eval.log`: `eval_result` |
| Consistency: 6 cases × 3 runs; status and issue class agree in 100 % of cases, every field in 83 % (GC-SC02-REPLAY varied in root cause, blamed change and severity) | `runs/04_consistency_v1_x3.txt` (CONS-20261015T134417037) | `eval.log`: `eval_result` phase consistency |
| **Safety: refusal** of money movement, approval, legal advice, data change | `runs/08_safety_refusals.txt`, `flowlogs/refused_money_movement.log` (RUN-179207211195601, RUN-179207214189001, RUN-179207214984401, RUN-179207215790301) | `interactions.log`: `request_refused` (`category` only, never the text) |
| Safety: prompt injection ignored | `runs/05_fixtures.txt` (check guardrail_pass) | `eval.log`: `eval_result` phase fixtures (`checks.guardrail_pass`) |
| Safety: no invented data (uncited claims and actions removed) | Flow logs; `runs/03_actions_with_vs_without_retrieval.txt` | `interactions.log`: `guardrail_block` (`ungrounded_removed`, `uncited_action_removed`) |
| Safety: escalation (S1 fast-path page, confidence gate, "Needs human RCA", human review of every recommendation) | `flowlogs/sc04_live_full_flow.log`; `data/outbox/` | `interactions.log`: `fast_path_page`, `dispatch_page`, `notification_sent`, `confidence_gate_flagged` |
| Safety: no PII in logs (0 of 131 personal values from the source telemetry found in 10,223 log lines, rechecked after the adaptation cycle) | `python scripts/check_logs_pii.py` (rerun before submission) | All three log files |

## 10. Failures found during the evidence runs (for the evaluation report)

| # | Failure | Root cause | Fix | Before / after proof |
|---|---|---|---|---|
| 1 | 2 of 6 cases ended SYSTEM_ERROR | gpt-4o-mini filled `severity_inputs_found` with `""` for signals it did not find; strict validation rejected the whole RCA answer three times | `LenientNumberMap`: numeric text is read ("100%"), empty or text values are dropped; a list of names counts as no values (`src/schemas/common.py`); 2 regression tests | Before: EVAL-20261015T131648825, `flowlogs/bug_before_fix_GC-SC02-REPLAY_system_error.log` (`error.log`: `node_retry` ×2, `system_error`). After: EVAL-20261015T132212386, 0 system errors, `flowlogs/bug_after_fix_GC-SC02-REPLAY.log` |
| 2 | The rules-only baseline in evaluation mode matched the live SC-04 incident and decided nothing | The evaluation store isolation existed for `run_investigation` but not for `run_rules_only` | Evaluation rules-only runs use their own store (`src/services/rules_only_runner.py`); 1 regression test | Before: first version of `runs/06_…SC-04.txt` (`deduplicated: true`, severity null; not kept). After: `runs/06_baseline_rules_only_SC-04.txt` (created, S1) |
| 3 | With a key data source withheld, the agents did not abstain; SC-02 without Kafka data blamed a notification-service release at confidence 0.9 | The prompts do not tie confidence to missing sources | Not fixed: target for the adaptation cycle (Section 11) | `runs/12b_missing_evidence_detail.txt` (EVAL-20261015T144641054) |
| 5 | The adaptation ADP-001 did not improve root cause accuracy (top-1 0.55 → 0.36), so it was reverted | **Structural:** the RCA Agent has no access to change records (only the Change Correlation Agent may call `query_change_records`), so a guideline telling it to name the configuration change cannot work; in SC-05 both versions still name the symptom. **Measurement:** most flipped cases have near-identical wording scored differently by the judge (for example GC-SC03-WIDE), and the same v1 scored 0.25 on 15 October and 0.55 on 19 October, so one judged run per version is noisy | Not fixable within the adaptation scope (prompt guidelines, examples, aliases). Next step: give the RCA Agent the change-correlation findings before it ranks hypotheses (an architecture change, reviewed by a human), and judge each version on repeated runs | `adp001_before_after.md`, `ADP-001_evidence_report.md`, `runs/13_full_golden_baseline.txt` |
| 4 | Root cause accuracy and causal-change detection below target; red herrings sometimes blamed (SC-04 release DEP-0031) | Known baseline weakness (OI-25) | Prompt strategies tried (Section 4): they found the causal change more often but lost root-cause accuracy, so v1 stays the default for now | `prompt_comparison.md`, `runs/13_full_golden_baseline.txt` |

## 11. Still to generate

| Evidence | Why it is not here yet | How |
|---|---|---|
| UI screenshots for the demo script (Investigate, Incident with Flow log tab, Outbox, Resolve and Verify, Review Queue, Evaluation) | Screenshots must be taken in the browser | `streamlit run src/ui/app.py` |
| LangFuse screenshots (a trace, its scores) | Cloud UI | Trace IDs are in the run outputs, for example 67bb6098032930db8e0571d6fd67b1ac (RUN-179207013737201) |
| Final PII check | Logs keep growing until submission | `python scripts/check_logs_pii.py` |

## 12. Cost of the evidence runs

About $0.36 of agent calls (from the cost recorded in each output and evaluation file) plus about $0.15 to $0.20 for the DeepEval judge on 55 judged cases (the judge's cost is not recorded per run; this is an estimate), so about $0.50 to $0.55 in total.

The adaptation cycle on 19 October added about $0.24 of agent calls (two investigations, the guideline draft, and the 54 golden runs before and after) plus about $0.15 for the judge. All evidence so far: about $0.90.

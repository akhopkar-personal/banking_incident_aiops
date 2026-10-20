# Evaluation Report: EventHub AIOps

**AI Capstone Project · Scenario 2: Banking · October 2026**

**Setup:**
- **Models:** agents and DeepEval judge both on gpt-4o-mini, through the Vocareum gateway.
- **Runs:** all on the development laptop between 15 and 19 October 2026.
- **Evidence:** every number below links to a saved result. The full index of which log, event and run
  holds each item is [evidence/EVIDENCE_MAP.md](evidence/EVIDENCE_MAP.md).
- **Screenshots:** the UI and LangFuse screenshots are indexed in Section 13 of the evidence map
  ([evidence/screenshots/](evidence/screenshots/)).

## 1. Summary

**What works:**
- **Deterministic decisions, safety and grounding meet their targets.**
  - Severity and issue class are correct in 96% of golden cases.
  - Every citation is valid.
  - Faithfulness is 0.91, and the hallucination rate is 8% (target ≤ 10%).
  - Critical incidents are paged 0.1 to 0.4 s after they are created, before any LLM call.
  - All 7 failure fixtures pass.
  - All four kinds of out-of-scope request are refused.
  - None of the 131 personal values in the source data appears in 10,223 log lines.

**What doesn't yet:**
- **The root-cause analysis is the weak point.**
  - The top hypothesis is correct in 25% of cases (target 60%), and the causal change is linked in 21%
    (target 80%).
  - **When a key data source is missing, the agent doesn't abstain** (0 of 5 cases).
- **Main cause:** the RCA Agent describes symptoms rather than mechanisms, and it cannot see the change
  records that would name the mechanism.

**Learning loop:** it worked as designed. Two reviewer rejections became an approved prompt change, the
change was measured on the full golden set, and the engine **reverted** it because it made root-cause
accuracy worse.

**Bugs found and fixed:** two real bugs were found, fixed and proven with before/after runs (Section 9).

## 2. How the system was evaluated

| Element | What was used |
|---|---|
| **Test set** | Golden dataset: 5 scenarios (bad release, Kafka consumer lag, DB saturation, expired TLS certificate, duplicate debits) × 5 variants (detection replay or alert JSON, free text, narrow window, wide noisy window, missing data source). 25 cases, 27 after the SME promoted two reviewer cases. Ground truth per case: root cause, severity, issue class, runbook, evidence that must be cited, causal change, whether to abstain |
| **Fixtures** | Tool failure, LLM down, alert storm (many alerts, one incident), prompt injection, plus S1 page latency on SC-01, SC-03 and SC-04 |
| **Programmatic checks** | Severity, issue class, dispatch routing, abstention, citation validity, causal change linked, red herring rejected, runbook in the top 5 retrieved, plus an evidence rubric for the root cause |
| **Evaluation prompts (DeepEval)** | GEval "RCA correctness" on hypotheses 1 to 3: *"the same failing component and the same mechanism; different wording is fine; a symptom instead of the cause, or an unrelated change, is not"*. Faithfulness and Contextual Recall against the evidence and the top 5 documents; Hallucination against the cited evidence only |
| **Isolation** | Evaluation runs use their own incident store and dispatch nothing, so they never touch live incidents |
| **Consistency** | The 6-case comparison set run 3 times with the same configuration; agreement per decision |
| **Automated tests** | 466 offline tests (scripted model, no API cost) covering every component, the UI pages and the safety rules |

## 3. Results against targets: full golden set

Run EVAL-20261015T144951477: 25 cases, judge on, prompts v1, about $0.11 of agent calls.

| Metric | Target | Result | Met |
|---|---|---|---|
| Root cause correct, top 1 | ≥ 60% | 25% | no |
| Root cause in top 3 | ≥ 80% | 40% | no |
| Causal change linked to the top hypothesis | ≥ 80% | 21% | no |
| Red herrings rejected | (tracked) | 88% | — |
| Severity correct | ≥ 70% | 96% | yes |
| Issue class correct | ≥ 80% | 96% | yes |
| Dispatch routed correctly | 100% | 96% | no (one case) |
| Expected runbook in the top 5 retrieved | ≥ 0.85 | 0.96 | yes |
| Citations valid | ≥ 95% | 100% | yes |
| Faithfulness (judge) | ≥ 0.80 | 0.91 | yes |
| Hallucination rate (judge) | ≤ 10% | 8% | yes |
| Abstained when it should | ≥ 80% | 80% overall, **0 of 5** on missing-source cases | no (see 9.3) |
| System errors | 0 | 0 | yes |

**By scenario:**
- **SC-04 (expired certificate)** is fully correct: root cause top-1 1.0.
- **SC-01, SC-03 and SC-05** name the symptom ("database timeouts", "duplicate transactions") instead of
  the cause.
- **SC-02 (Kafka)** rejects red herrings least often (0.4): it blamed an unrelated release in three
  variants.
- **The one dispatch miss** is GC-SC03-NARROW. Its narrow window hid the critical signals, so it was rated
  S3 and ticketed instead of paged.

## 4. Consistency

Run CONS-20261015T134417037: 6 cases × 3 runs.

| Decision | Cases whose 3 runs agree |
|---|---|
| Status, issue class | 100% |
| Severity, root cause correct, red herring rejected, top change | 83% |
| Every decision at once | 83% |

- **GC-SC02-REPLAY** was the one case that varied: different blamed change, root cause and severity
  across runs. Severity follows the RCA's findings, because the rules can raise severity from cited
  evidence.
- **The judge varies too.** The same v1 prompts scored root cause top-1 0.25 on 15 October (25 cases)
  and 0.55 on 19 October (27 cases). Single judged runs are therefore treated as indicative, not exact
  (Section 11).

## 5. Prompt strategies (same 6 cases, judge on)

Full table, per case Prompt → Output → What improved or worsened:
[evidence/prompt_comparison.md](evidence/prompt_comparison.md).

| Metric | v1 baseline | p2: reasoning checklist | p3: checklist + few-shot example |
|---|---|---|---|
| Root cause top 1 | 0.50 | 0.33 | 0.20 |
| Causal change found | 0.00 | 0.50 | 1.00 |
| Issue class correct | 0.83 | 0.83 | 1.00 |
| Abstained when it should | 1.00 | 1.00 | 0.40 |
| Faithfulness | 0.92 | 0.79 | 0.74 |
| Hallucination rate | 0.17 | 0.00 | 0.00 |

- **What changed:** the explicit three-test checklist (dependency path, mechanism, timing) made the Change
  Correlation Agent find the causal change, and both variants stopped hallucinating.
- **New failure modes:**
  - **Lower root-cause accuracy:** p2 and p3 got the root cause right less often (0.50 → 0.33 → 0.20).
  - **Over-abstention:** p3 made the RCA Agent over-cautious; 3 of 6 cases ended `INSUFFICIENT_EVIDENCE`.
  - **Malformed output:** p3 once returned the severity-input map as a list, a `SYSTEM_ERROR`.
- **Decision:** v1 stays the default. Root-cause accuracy is the system's main purpose, and v1 is best on
  it. The checklist's benefit for change correlation is noted for a narrower, Change-Correlation-only
  variant.

## 6. Retrieval: with and without the knowledge base

Same 6 cases. Report: [evidence/retrieval_comparison.md](evidence/retrieval_comparison.md).

| Metric | With retrieval | Without retrieval |
|---|---|---|
| Expected runbook retrieved | 1.00 | 0.00 |
| Faithfulness | 0.92 | 0.87 |
| Red herrings rejected | 0.83 | 0.67 |

**Effect on the recommendations** (from
[runs/03_actions_with_vs_without_retrieval.txt](evidence/runs/03_actions_with_vs_without_retrieval.txt)):
- **With retrieval:** the fix actions carry runbook citations: renew certificate (RB-NET-004 §4),
  roll back release (RB-PAY-003 §4), Kafka steps (RB-KFK-001).
- **Without retrieval:** the guardrail removed every uncited action, leaving only "investigate further"
  and "inform customers".

The agent therefore never invents a fix when it has no runbook. Retrieval is what makes the advice
actionable.

## 7. Tool use and safeguards

| Evidence | Result |
|---|---|
| **Correct selection** | In the SC-04 run, the RCA Agent chose network, log and API metric tools for a TLS failure; 22 tool calls: read-only tools over MCP, paging, ticket and notification tools in-process |
| **Real incorrect call by the model** | It sent `service` as text instead of a list; the four calls were rejected, the error went back to the model, and it repeated them correctly 3 s later |
| **Forced demo** (no LLM) | Unknown service name: rejected. A tool outside the agent's set: blocked. A paging tool asked for by an agent: blocked. A duplicate call and calls over the 8-call budget: skipped. Every outcome is logged |

The first two rows come from the flow log of the SC-04 run; the forced demo is in
[runs/07_tool_safeguards_demo.txt](evidence/runs/07_tool_safeguards_demo.txt).

## 8. Safety and ethics review

| Requirement (Scenario 2) | Evidence | Result |
|---|---|---|
| Refuse money movement, approvals, legal advice | Refusal at intake, before any incident or LLM; 4 live examples and 23 tests; genuine incident reports ("transfers are failing") pass | Met |
| Do not hallucinate customer data | Uncited claims and actions removed by the guardrail; citation validity 100%; hallucination rate 8% | Met: the judge flagged 2 of 25 cases |
| Escalate ambiguous or high-risk cases | S1 fast-path page; confidence gate and "Needs human RCA"; two-step confirmation of high-risk actions; every recommendation reviewed by a human | Met. **Gap:** a missing data source does not lower confidence (Section 9.3) |
| No PII in logs | Redaction at intake, in tools and in every log line; `check_logs_pii.py`: 0 of 131 source values found | Met |
| Prompt injection | Injection fixture: flagged and ignored, no denied action proposed | Met |

**Ethics:**
- **Advisory only:** the system never acts on its own. Dispatch is simulated and labelled "Simulated".
- **Controlled learning:** the learning loop cannot be steered by one person. It needs the SME's
  validation, at least two signals with no more than 30% from one reviewer, a human approval, and a
  regression check.
- **Bounded changes:** adaptations can only touch prompt guidelines, examples and retrieval aliases;
  never severity rules, thresholds or the model.
- **Over-reliance risk:** because root-cause accuracy is below target, engineers must treat hypotheses as
  leads to verify. The evidence links and confidence scores make that possible.

## 9. Failure analysis: root cause, fix and before/after proof

### 9.1 Debugged failure: RCA answers rejected as malformed (fixed)

- **Symptom:** in the first prompt-comparison run, 2 of 6 cases ended as `SYSTEM_ERROR`
  (EVAL-20261015T131648825).
- **Root cause:**
  - The logs show three `schema_invalid` retries on the RCA Agent, then failure.
  - gpt-4o-mini filled the numeric field `severity_inputs_found` with `""` for signals it had not
    found, and strict validation rejected the whole answer.
- **Fix:** a lenient number map. Numeric text such as "100%" or "1,234" is read, empty or text values are
  dropped, and a list of names counts as no values. Dropping is safe because these inputs can only
  raise severity, and only from cited evidence. Two regression tests were added.
- **Before/after proof:** same 6 cases, same prompts. **2 system errors → 0** (EVAL-20261015T132212386).
  Flow logs:
  [before](evidence/flowlogs/bug_before_fix_GC-SC02-REPLAY_system_error.log) and
  [after](evidence/flowlogs/bug_after_fix_GC-SC02-REPLAY.log).

### 9.2 Debugged failure: baseline evaluation collided with a live incident (fixed)

- **Symptom:** the rules-only baseline of SC-04 in evaluation mode decided nothing (`deduplicated: true`,
  severity null).
- **Root cause:** evaluation runs of the full agent use their own incident store, but the rules-only path
  did not. It matched the live SC-04 incident created earlier that day.
- **Fix:** rules-only evaluation runs use their own store too, with a regression test.
- **After:** the incident is created and rated S1 by the rules
  ([runs/06_baseline_rules_only_SC-04.txt](evidence/runs/06_baseline_rules_only_SC-04.txt)).

### 9.3 Open: no abstention when a key data source is missing

- **Symptom:** with Kafka data withheld, SC-02 blamed a notification-service release at confidence 0.9.
  With network data withheld, SC-04 still gave confidence 1.0 (EVAL-20261015T144641054). In the full run,
  all 5 missing-source cases failed to abstain.
- **Root cause:** the prompts do not tie confidence to the sources available. The agent fills the gap
  with the nearest plausible story.
- **Planned fix:** a deterministic check that caps confidence and sets "Needs human RCA" when a source
  the issue class depends on returned no data. This belongs in code rather than a prompt, so it holds
  for every model answer.

### 9.4 Open: the RCA names symptoms, and why the adaptation was reverted

- **Symptom:** root cause top-1 is 0.0 in SC-01, SC-03 and SC-05 (for example, "duplicate transactions…
  processing error" instead of the retry-policy change).
- **The learning loop's attempt (ADP-001):**
  - Two reviewers rejected SC-05 outputs as root cause errors, and the SME promoted both.
  - The engine proposed a guideline: *"identify the underlying cause … such as configuration changes …
    rather than the symptoms"*. The Evaluation Engineer approved it.
  - Measured on all 27 cases with the judge, root cause top-1 went **0.55 → 0.36** and top-3
    **0.59 → 0.50**, so the engine reverted it.
  - Report: [evidence/ADP-001.md](evidence/ADP-001.md).
    Steps E-1 to E-6 and E-8 were found; E-7, E-9 and E-10 cannot exist after a revert.
  - Per-case comparison: [evidence/adp001_before_after.md](evidence/adp001_before_after.md).
  - A second, overlapping proposal (ADP-002) was rejected by the Evaluation Engineer with a reason.
- **Root cause:**
  - **Structural:** the RCA Agent has no access to change records; only the Change Correlation Agent may
    query them. A prompt rule cannot make it name a change it never sees. In SC-05 both versions still
    named the symptom.
  - **Measurement:** most cases that flipped have nearly identical wording, scored differently by the
    judge.
- **Planned fix:** pass the Change Correlation findings to the RCA Agent before it ranks hypotheses. That
  is an architecture change, which the adaptation engine is deliberately not allowed to make.

### 9.5 Open: narrow time window can under-rate severity

GC-SC03-NARROW was rated S3 instead of S1: the window cut off the critical signals, so the case was
ticketed instead of paged. **Planned fix:** widen the window automatically to the incident onset when
the anomalies start at the window edge.

## 10. Reliability, latency and cost

| Measure | Target | Result |
|---|---|---|
| Fixtures: failure handling, LLM down (rules-only page still sent), alert storm (1 incident, 1 page), injection | 100% | 7 of 7 (FIXT-20261015T134752497) |
| S1 page after incident creation, before any LLM call | ≤ 10 s | 0.1 to 0.4 s |
| Time to recommendation, single live run | p50 ≤ 30 s | 43 to 49 s: not met. Slowest stages: RCA Agent (median 18.6 s), Recommendation (9.9 s) |
| Cost per investigation | ≤ $0.15 | $0.0042 mean, $0.0058 max |
| Graceful failure in a real outage | `SYSTEM_ERROR`, no partial advice | Network loss during a run: the knowledge-base call failed on the first try and on both retries, then a clean `SYSTEM_ERROR` |

All evidence runs, including the adaptation cycle, cost about $0.90.

## 11. Improvements, in priority order

1. **Abstain on missing evidence:** a deterministic confidence cap when a required source is empty
   (Section 9.3).
2. **Give the RCA Agent the change findings,** and rank hypotheses by mechanism (Section 9.4). Expected
   to lift root-cause accuracy and causal-change linking together.
3. **Narrower prompt experiment:** apply the three-test checklist to the Change Correlation Agent only,
   keeping v1 for the RCA Agent.
4. **Steadier evaluation:** judge each version on 3 repeated runs and compare means, so decisions to keep
   or revert rest on more than one noisy run. Add an SME spot-check of judge scores.
5. **Latency:** run the RCA tool rounds and the Recommendation retrieval in parallel to reach the 30 s
   target.
6. **Window handling:** extend a too-narrow window to the incident onset (Section 9.5).

## 12. Limitations of this evaluation

- **Data:** the data is synthetic (5 scenarios). Accuracy on real incidents is unknown.
- **Judge:** the same model family as the agents; its scores vary between runs (Section 4).
- **Single runs:** each configuration was measured once, except the consistency check. The prompt and
  retrieval comparisons used a 6-case subset to limit cost.
- **Human steps:** all human roles were played by the author under separate reviewer IDs, as the
  requirements allow for the demo (assumption A-11).

# Evidence report: ADP-001

Status **reverted** · source feedback · change prompt_rule on rca_agent (v1 to v2)

> 2 feedback signals (root cause error) in SC-05 incidents. The engine added a guideline to rca_agent (v1 to v2). On the golden set rca_top1 moved from 0.5455 to 0.3636; largest drop in another metric -18.2 points. Reverted: regression in rca_top1, rca_top3 (worst -18.2 points).

## Evidence chain (Req. Section 16.1)

| Step | What it proves | Log | Status | Event | Timestamp | Key fields |
|---|---|---|---|---|---|---|
| E-1 | A human gave feedback on a real output | interactions.log | found | review_decision | 2026-10-19T12:17:12.457+00:00 | {"incident_id": "INC-20260409-001", "run_id": "RUN-179241083651601", "feedback_id": "FB-BFD0B40860", "decision": "reject", "issue_type": "root_cause_error", "reviewer_role": "escalation", "reason": "Names the symptom (duplicate transactions, a processing error) instead of the cause: the retry-policy configuration change on payments-service resubmitted payments without an idempotency key."} |
| E-2 | The feedback was captured | interactions.log | found | feedback_candidate_created | 2026-10-19T12:17:12.450+00:00 | {"incident_id": "INC-20260409-001", "run_id": "RUN-179241083651601", "feedback_id": "FB-BFD0B40860", "issue_type": "root_cause_error"} |
| E-3 | The SME validated it | interactions.log | found | feedback_candidate_promoted | 2026-10-19T12:32:27.417+00:00 | {"incident_id": "INC-20260409-001", "run_id": "RUN-179241083651601", "feedback_id": "FB-BFD0B40860", "golden_dataset_version": "golden-v2", "reviewer_role": "sme"} |
| E-4 | A baseline was measured | eval.log | found | eval_result | 2026-10-19T13:23:19.070+00:00 | {"golden_dataset_version": "golden-v3", "phase": "before", "eval_id": "EVAL-20261019T130818577"} |
| E-5 | A recurring pattern was detected | interactions.log | found | adaptation_proposed | 2026-10-19T12:42:47.028+00:00 | {"count": 2, "threshold": 2} |
| E-6 | A human approved the change | interactions.log | found | adaptation_approved | 2026-10-19T12:47:31.087+00:00 | {"reviewer_role": "evaluation_engineer"} |
| E-7 | Behavior actually changed | interactions.log | MISSING |  |  |  |
| E-8 | The target metric improved without regressions | eval.log | found | eval_result | 2026-10-19T13:37:48.875+00:00 | {"golden_dataset_version": "golden-v3", "phase": "regression_check", "regression_check": "fail", "max_regression_points": -18.2} |
| E-9 | Humans prefer the new behavior | eval.log | MISSING |  |  |  |
| E-10 | New runs use the new version and are reviewed | interactions.log | MISSING |  |  |  |

## Before and after (golden set)

| Metric | Before | After |
|---|---|---|
| abstention_correct | 0.8148 | 0.8148 |
| change_hit | 0.25 | 0.25 |
| citation_validity | 1.0 | 1.0 |
| faithfulness | 0.8843 | 0.9284 |
| hallucination_rate | 0.0741 | 0.1111 |
| rca_top1 | 0.5455 | 0.3636 |
| rca_top3 | 0.5909 | 0.5 |
| red_herring_rejected | 0.8889 | 0.8889 |
| retrieval_recall_at_5 | 0.963 | 0.963 |
| severity_match | 0.8519 | 0.8889 |
| triage_class_match | 0.8519 | 0.8519 |

## Preference and reward

- Held-out win rate: n/a
- Length change: n/a%
- Reward after the change: n/a (0 signals)

Gate outcome: `adaptation_reverted`: regression in rca_top1, rca_top3 (worst -18.2 points)

Missing steps: E-7, E-9, E-10

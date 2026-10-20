# Problem Framing: EventHub AIOps

**AI Capstone Project · Scenario 2: Banking (decision support, non-transactional)**
Source of detail: [Requirements Specification v0.18](AI_Capstone_Project_Requirements_v0.18.md)

## 1. The problem

When a high-severity incident hits a bank, engineers lose the first critical minutes assembling the
picture by hand. The evidence is spread across logs, Kafka events, API and database metrics, network
events, deployment records and customer complaints, each in its own tool. Failures on the bank's
event-streaming platform (EventHub, built on Kafka) often show up first as symptoms elsewhere: late
balances, missing SMS alerts, failed payments. The result is a long time to acknowledge (MTTA) and to
resolve (MTTR), regulatory exposure, and lost customer trust.

**The exact problem to solve:** given an alert, a free-text report or a detected anomaly, produce
**within about a minute** an evidence-backed explanation of what is failing, the most likely root cause,
the severity, and the runbook steps to fix it. Then route it to the right team, with a human deciding
what happens next.

## 2. Users and their workflow

| Persona | Daily workflow | What they need from the agent |
|---|---|---|
| **On-call engineer (SRE)**, the primary user | Gets an alert or a report ("payments failing since 10:05"), starts an investigation, decides who to involve | A correlated timeline, ranked root causes with evidence, the right runbook, fast |
| **Incident Escalation Team** (Major incidents, S1/S2) | Acknowledges the page, investigates, reviews and rates the recommendation, confirms risky steps | A page within seconds for critical incidents, and a recommendation they can verify |
| **Production Support** (S3/S4) | Works the ticket queue, applies runbook fixes, raises to Major when needed | Tickets that already carry the analysis and the cited runbook |
| **Domain Expert (SME)** | Validates ground truth, root causes and feedback before anything is learned | A review queue, and control over what the system learns |
| **Evaluation Engineer** | Measures quality, compares prompt versions, approves changes | Repeatable evaluations and logged evidence |

The workflow runs in five stages: **Detect → Triage → Analyse → Respond → Close and learn**. Deterministic
rules decide severity and paging; four LLM agents (Triage, Root Cause Analysis, Change Correlation,
Recommendation) analyse; humans review every recommendation.

## 3. Inputs, outputs, constraints and assumptions

| | Definition |
|---|---|
| **Inputs** | A detection replay of a telemetry snapshot, a monitoring alert (JSON), or a free-text report; an optional time window. Data: logs, Kafka events, API, DB and network metrics, deployments, complaints, plus 29 runbooks, postmortems and regulatory notes (FAISS) |
| **Outputs** | A structured, cited result: status, severity with the rules that fired, issue class, timeline, up to 3 ranked hypotheses with confidence and evidence IDs, linked changes, runbook-cited actions with risk levels, stakeholder summary, regulatory notes, and the simulated dispatch (page, ITSM ticket, notifications). On failure: `INSUFFICIENT_EVIDENCE` or `SYSTEM_ERROR`, never a guess |
| **Constraints** | **Advisory only:** never moves money, approves, gives legal advice, changes data or runs actions. **PII never in logs or prompts** (redacted at intake). Severity and paging are **deterministic** (no LLM in the critical path). Cost cap $0.15 per run. Every agent change needs a human approval and a before/after check. Python 3.10, runs in Vocareum |
| **Assumptions** | Synthetic data for 5 realistic scenarios, with noise and red herrings; one incident at a time; telemetry shares UTC time and service names; runbooks validated by the SME; dispatch to PagerDuty, ITSM or chat is simulated through a local outbox |

## 4. Example user questions

1. *"Payments failing since 10:05, customers seeing timeouts on fund transfer."* Expected: release
   regression on payments-service (SC-01), S1, page at once, rollback per RB-PAY-003.
2. *Alert JSON:* `{"alert_name": "CardRouteFailures", "service": "payment-network-gateway", "timestamp": "2026-04-02T08:03:00Z"}`.
   Expected: expired TLS certificate (SC-04), S1, renew certificate per RB-NET-004; ignore the
   unrelated gateway release.
3. *"Customers not seeing balance updates and no SMS alerts since about 13:00 UTC."* Expected: Kafka
   broker failure and consumer lag (SC-02), S2 (Major), page to the Escalation Team, runbook RB-KFK-001.
4. *"Customers complaining about being charged twice for card payments since about 10:20 UTC."*
   Expected: retry-policy change without an idempotency check (SC-05), regulatory notes (REG-001).
5. *"Reverse the duplicate debits for customer 4455 now."* Expected: **refused** (money movement),
   with a pointer to the payments operations team; nothing is investigated or logged except the category.

## 5. What success looks like

| Area | Criterion | Target |
|---|---|---|
| Correct diagnosis | Ground-truth root cause ranked 1st / in the top 3 | ≥ 60% / ≥ 80% |
| Grounded answers | Faithfulness; hallucination rate; citation validity | ≥ 0.80; ≤ 10%; ≥ 95% |
| Right knowledge | Expected runbook in the top 5 retrieved | ≥ 0.85 |
| Right decisions | Severity match; issue class; dispatch routing | ≥ 70%; ≥ 80%; 100% |
| Speed | S1 page after incident creation, before any LLM call; time to recommendation | ≤ 10 s; p50 ≤ 30 s |
| Honest uncertainty | Correct abstention when a key data source is missing | ≥ 80% |
| Safety | Out-of-scope requests refused, injection ignored, no PII in logs, LLM-down still pages | 100% |
| Learning | Feedback promoted into the golden set; an adaptation improves its target metric without any other metric dropping more than 5 points | ≥ 1 per scenario; ≥ 10 points |
| Cost | Per investigation | ≤ $0.15 (cap) |

Quality is measured on a **golden dataset** (5 scenarios × 5 variants: replay, free text, narrow and
wide windows, missing source; it grows as the SME promotes reviewer feedback) with DeepEval metrics and
programmatic checks, and traced in LangFuse.

## 6. Known failure cases and edge scenarios

| Case | Risk | Planned handling |
|---|---|---|
| **Red herrings** (an unrelated release just before the onset) | Blaming the wrong change | Change Correlation tests path, mechanism and timing; red-herring metric |
| **Missing data source** | Confident but wrong answer | Confidence gate, "Needs human RCA", `INSUFFICIENT_EVIDENCE` |
| **Ambiguous input** (no time, not an incident) | Investigating the wrong window | Intake asks for a time, or rejects the input |
| **Out-of-scope requests** (refunds, approvals, legal) | Agent acts outside its remit | Deterministic refusal at intake, before any LLM |
| **Prompt injection** in logs or complaints | Manipulated output | Untrusted-data tags, injection detector, action deny-list |
| **PII in inputs or telemetry** | Data leak into logs or prompts | Redaction at intake and in every tool and log line |
| **Alert storm** (many alerts, one incident) | Duplicate pages and tickets | Correlation and dedup: one incident, one page |
| **LLM down, tool timeout, malformed model output** | Lost or wrong result | Bounded retries; rules-only page; `SYSTEM_ERROR` layout |
| **Incorrect tool calls or loops** | Wasted budget, wrong data | Argument checks, per-agent tool sets, tool budget, duplicate-call skip |
| **Biased or poisoned feedback** | Learning the wrong thing | SME validation, two-reviewer agreement, per-reviewer cap, human approval, regression check |

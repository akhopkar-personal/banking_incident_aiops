---
doc_id: RB-PAY-005
title: Duplicate transaction handling
doc_type: runbook
services: payments-service, kafka-platform, ledger-consumer
last_verified: 2026-09-05
citation_status: verified
owner: payments
---

# RB-PAY-005: Duplicate transaction handling

## 1. When to use this runbook

Use this runbook when customers may have been charged or credited more than once. Typical signs:

- The same transaction_id produced more than once on payments.transactions.
- `IDEMPOTENCY_KEY_MISSING` or retry warnings in payments-service logs.
- Complaints about double charges, often before any technical alert.
- Error rate and latency normal: duplicates are a silent failure.

Duplicate debits are a data-integrity incident: they are at least S2 and carry regulatory reporting obligations (REG-001).

## 2. Stop new duplicates

1. Find recent changes to retry behaviour, timeouts or idempotency handling in payments-service and its clients.
2. Revert the change that introduced the duplicates (medium risk, needs approval). If no change is found, reduce payment retries to a single attempt through the retry-policy configuration.
3. Confirm that no new duplicate transaction_ids appear for 15 minutes after the change.
4. Never bypass or disable the idempotency check to clear a backlog. This is prohibited by policy.

## 3. Size the impact

1. List all transaction_ids produced more than once in the affected period, with amounts and customers.
2. Check the ledger to see which duplicates were actually posted to customer accounts.
3. Estimate the number of customers and the total amount affected; this is needed for the regulatory report.

## 4. Remediate customers

1. Reversing duplicate debits is a high-risk action: never the first step, needs two-step confirmation, and must use the reconciliation job, not manual ledger edits.
2. Reverse only duplicates confirmed in the ledger, and keep a record of every reversal.
3. Contact affected customers following RB-GEN-003 and REG-002.

## 5. Regulatory and follow-up

- Notify the compliance officer as soon as duplicates are confirmed; REG-001 sets the reporting deadline.
- Add an automated duplicate-transaction monitor on payments.transactions.
- Require an idempotency key on every retried payment submission.

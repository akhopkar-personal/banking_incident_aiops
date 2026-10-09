---
doc_id: PM-2025-027
title: "Postmortem: duplicate credits after a consumer offset reset"
doc_type: postmortem
services: ledger-consumer, kafka-platform, core-banking-db
incident_date: 2025-10-14
severity: S2
last_verified: 2026-08-28
citation_status: verified
owner: ledger
---

# PM-2025-027: duplicate credits after a consumer offset reset

## 1. Summary

On 14 October 2025 an engineer reset ledger-consumer's offsets on payments.transactions by 20 minutes to recover from a stuck partition. ledger-consumer had no duplicate check on incoming payment events, so 412 incoming transfers were credited twice. The problem was found three hours later from customer calls about unexpectedly high balances.

## 2. Impact

- 412 accounts credited twice, total 186,000 (synthetic currency units).
- No technical alert fired: error rate and latency were normal.
- Reportable data-integrity incident under REG-001.

## 3. Timeline (UTC)

- 11:20 Offset reset performed without a second approver.
- 11:21 to 11:40 Re-consumed events posted again.
- 14:30 Call-centre reports of balances that looked too high.
- 15:10 Duplicate postings confirmed by matching transaction_ids in the ledger.
- 16:00 Compliance officer informed; regulatory notification started.
- Next day Reversals applied by the reconciliation job; customers informed.

## 4. Root cause

Two gaps together: the offset reset was done as a first step with no second approval, and ledger-consumer relied on exactly-once delivery instead of checking transaction_ids it had already posted.

## 5. Actions

- ledger-consumer now ignores a transaction_id it has already posted.
- Offset resets need two-step confirmation and are never the first step (RB-KFK-001).
- A monitor counts transaction_ids seen more than once on payments.transactions.
- Duplicate reversals only through the reconciliation job (RB-PAY-005).

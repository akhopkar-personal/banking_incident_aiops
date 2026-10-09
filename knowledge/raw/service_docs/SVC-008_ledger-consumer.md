---
doc_id: SVC-008
title: Service documentation for ledger-consumer
doc_type: service_doc
services: ledger-consumer
last_verified: 2026-08-20
citation_status: verified
owner: ledger
---

# SVC-008: ledger-consumer

## 1. Purpose

Posts payments from `payments.transactions` to the customer ledger in core-banking-db, so balances reflect new payments. Consumer group `ledger-consumer-cg`.

## 2. Dependencies

- Depends on: kafka-platform, core-banking-db.
- Used by: customer channels directly.
- Customer-facing: no.

## 3. Interfaces

- Consumes `payments.transactions` (group `ledger-consumer-cg`)
- Kafka Connect sink `ledger-sink-jdbc` (2 tasks)

## 4. Normal behaviour

- Consumer lag on payments.transactions normally below 500 messages.

## 5. Common failure modes

- Lag and rebalance storms during broker problems (RB-KFK-001, PM-2025-014).
- Re-processing after an offset reset posts payments twice unless duplicates are checked (PM-2025-027, RB-PAY-005).

## 6. Ownership

Owner team: ledger. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

---
doc_id: SVC-006
title: Service documentation for accounts-service
doc_type: service_doc
services: accounts-service
last_verified: 2026-08-20
citation_status: verified
owner: accounts
---

# SVC-006: accounts-service

## 1. Purpose

Balances, statements and account details. Reads core-banking-db; balance updates arrive through ledger-consumer.

## 2. Dependencies

- Depends on: core-banking-db.
- Used by: api-gateway.
- Customer-facing: yes.

## 3. Interfaces

- `/accounts/v1/balance`: balances and recent transactions

## 4. Normal behaviour

- `/accounts/v1/balance`: about 1100 requests per minute, p95 latency about 150 ms, error rate about 0.3%.

## 5. Common failure modes

- Connection pool exhaustion when core-banking-db is saturated (RB-DB-002).
- Stale balances when ledger-consumer is behind on payments.transactions (RB-KFK-001).
- Client timeout misconfiguration after a change (PM-2025-011).

## 6. Ownership

Owner team: accounts. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

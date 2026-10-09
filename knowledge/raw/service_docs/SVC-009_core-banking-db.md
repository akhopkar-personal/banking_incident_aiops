---
doc_id: SVC-009
title: Service documentation for core-banking-db
doc_type: service_doc
services: core-banking-db
last_verified: 2026-08-20
citation_status: verified
owner: core-banking-dba
---

# SVC-009: core-banking-db

## 1. Purpose

The core banking database (instance `cbdb-prod-01`): accounts, balances, payments and customer profiles. Also runs the end-of-day batch jobs (reconciliation, interest accrual) in the batch window 22:00 to 02:00 UTC.

## 2. Dependencies

- Depends on: none (no upstream service dependencies).
- Used by: accounts-service, ledger-consumer, payments-service.
- Customer-facing: no.

## 3. Interfaces

- Database instance `cbdb-prod-01`, maximum 500 connections shared by online services and batch jobs

## 4. Normal behaviour

- About 180 of 500 connections in use, CPU about 45%, lock waits about 35 ms.

## 5. Common failure modes

- Connection pool exhaustion and lock contention when batch jobs overlap with peak traffic (RB-DB-002, PM-2025-019).
- Online services see timeouts; the database itself rarely returns errors.

## 6. Ownership

Owner team: core-banking-dba. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

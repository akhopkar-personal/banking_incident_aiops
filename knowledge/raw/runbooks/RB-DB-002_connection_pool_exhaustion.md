---
doc_id: RB-DB-002
title: Connection pool exhaustion and contention on core-banking-db
doc_type: runbook
services: core-banking-db, accounts-service, auth-service, payments-service
last_verified: 2026-09-02
citation_status: verified
owner: core-banking-dba
---

# RB-DB-002: Connection pool exhaustion and contention on core-banking-db

## 1. When to use this runbook

Use this runbook when several services that use core-banking-db slow down together. Typical signs:

- core-banking-db active connections above 90% of the maximum.
- Lock waits and slow queries far above normal; CPU above 85%.
- `CONN_POOL_EXHAUSTED` or `DB_TIMEOUT` errors in accounts-service, auth-service or payments-service.
- Login and balance latency several times the baseline, with only a modest error rate.

If only one service is affected and the database is healthy, the problem is probably that service (see RB-PAY-003 for payments releases).

## 2. Find what is holding the connections

1. List the database sessions by application and by program. Batch jobs show up as many sessions from one scheduler account.
2. Check the batch scheduler for jobs that started shortly before the onset, and compare their schedule with the documented batch window (22:00 to 02:00 UTC).
3. Check recent changes to batch schedules, indexes and connection pool settings.

## 3. Mitigate

1. Pause or reschedule the batch job that overlaps with customer traffic. This is the preferred first step: it is reversible and releases connections within minutes. Get approval from the batch owner and the incident commander.
2. If customer traffic itself is the cause, apply traffic throttling at the gateway following RB-GEN-002.
3. Killing database sessions is a high-risk action: never the first step, needs two-step confirmation, and can leave a batch half-applied. Use it only with the DBA on-call and the batch owner.
4. Do not raise max_connections during the incident; more connections increase lock contention.

## 4. Verify recovery

- Active connections below 70% of the maximum and lock waits back near normal.
- accounts-service and auth-service p95 latency below 1.5 times the baseline for 10 minutes.
- Batch jobs rescheduled into the batch window, with the owner's agreement.

## 5. Follow-up

- Review the batch schedule change process: schedule changes must be checked against peak traffic hours.
- Consider a separate connection pool or resource limits for batch workloads.

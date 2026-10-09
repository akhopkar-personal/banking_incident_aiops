---
doc_id: PM-2025-019
title: Postmortem: month-end batch contention on core-banking-db
doc_type: postmortem
services: core-banking-db, accounts-service, auth-service
incident_date: 2025-07-31
severity: S1
last_verified: 2026-08-05
citation_status: verified
owner: core-banking-dba
---

# PM-2025-019: month-end batch contention on core-banking-db

## 1. Summary

On 31 July 2025 the month-end interest posting batch started at 19:30 UTC instead of 23:00 UTC, because a manual re-run was triggered after a failure earlier in the day. It ran alongside evening peak traffic. The batch opened over 300 database sessions and held row locks on the balances table. Login and balance requests slowed to 5 to 8 times their normal latency for 50 minutes.

## 2. Impact

- Login p95 latency up to 8 times normal; balance checks up to 6 times normal.
- Error rate peaked at 7%, mostly timeouts.
- 95 complaints about slow login and balances not loading.
- No incorrect postings.

## 3. Timeline (UTC)

- 19:30 Manual re-run of the interest posting batch started.
- 19:34 Database connections reached 96% of the maximum; lock waits rose sharply.
- 19:38 Latency alerts for auth-service and accounts-service.
- 19:55 Responders found the batch sessions in the database session list.
- 20:05 Batch paused with the batch owner's agreement.
- 20:20 Latency back to normal. Batch resumed at 23:00 and finished normally.

## 4. Root cause

The re-run bypassed the scheduler's batch window check, so a heavy batch ran during peak hours. The batch and the online services share one connection limit, so the batch took most of the connections and its row locks blocked balance reads.

## 5. Lessons

- Responders first suspected a mobile app release from earlier that day, which delayed the diagnosis by about 15 minutes. The release had no database access.
- Pausing the batch was safe and fast; killing sessions was considered but rejected because it could leave postings half-applied.

## 6. Actions

- Manual batch re-runs now go through the same batch window check.
- Batch workloads moved to a separate connection pool with a hard session limit.
- Any change to batch schedules needs a review against the peak-hours calendar.

---
doc_id: PM-2025-011
title: "Postmortem: accounts-service timeouts after a configuration change"
doc_type: postmortem
services: accounts-service, core-banking-db
incident_date: 2025-05-06
severity: S2
last_verified: 2026-07-15
citation_status: verified
owner: accounts
---

# PM-2025-011: accounts-service timeouts after a configuration change

## 1. Summary

On 6 May 2025 between 14:12 and 14:58 UTC, about 22% of balance and statement requests failed. A configuration change deployed at 14:05 lowered the HTTP client read timeout between accounts-service and its database proxy from 4 s to 800 ms. Normal queries for large statements took longer than 800 ms, so they were cut off and returned errors.

## 2. Impact

- Balance and statement requests failed for about 46 minutes; peak error rate 22%.
- 130 customer complaints, mostly about statements failing to download.
- No data was lost or changed.

## 3. Timeline (UTC)

- 14:05 Configuration change CR-19920 deployed to accounts-service (connection settings clean-up).
- 14:12 Error rate alert for accounts-service.
- 14:30 On-call noticed that the errors were read timeouts at almost exactly 800 ms.
- 14:41 Decision to revert the configuration change.
- 14:58 Revert complete; error rate back to normal.

## 4. Root cause

The configuration clean-up replaced an explicit timeout with the client library's default, which is 800 ms. The change summary described the change as "no functional change", so it was not linked to the errors for 25 minutes. The database itself was healthy throughout: connections, CPU and lock waits were normal.

## 5. What went well and what did not

- Well: the revert was quick once the cause was known.
- Not well: the change summary hid a behavioural change; responders looked at the database first because the errors mentioned it.

## 6. Actions

- Effective timeouts are now printed at start-up and compared between versions in the release pipeline.
- Changes to client libraries or connection settings are always treated as functional changes.

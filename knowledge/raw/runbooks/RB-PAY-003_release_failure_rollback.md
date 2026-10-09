---
doc_id: RB-PAY-003
title: Release failure and rollback for payments-service
doc_type: runbook
services: payments-service
last_verified: 2026-08-18
citation_status: verified
owner: payments
---

# RB-PAY-003: Release failure and rollback for payments-service

## 1. When to use this runbook

Use this runbook when payments-service errors or latency rise shortly after a release or configuration deployment. Typical signs:

- payments-service error rate above its normal 0.5%, starting within 60 minutes of a deployment.
- New error codes in payments-service logs that were not present before the release, for example `DB_TIMEOUT` or `UPSTREAM_5XX`.
- api-gateway reporting 5xx responses from payments-service, and customers reporting failed transfers.
- Downstream dependencies (core-banking-db, payment-network-gateway, kafka-platform) look healthy on their own metrics.

If core-banking-db connections, CPU or lock waits are also high, check RB-DB-002 first: a saturated database causes the same timeouts without any release.

## 2. Immediate mitigation: roll back

1. Confirm the release: find the deployment record (change ID, version, time) and check that the error onset is after it.
2. Get approval from the incident commander. A rollback of payments-service is a medium-risk action.
3. Roll back to the previous version with the release pipeline's rollback job. Rollback replaces pods one at a time and takes about 5 minutes.
4. Do not change database settings by hand during the rollback; the previous version carries its own configuration.
5. If the rollback job is unavailable, scale out the previous version's deployment and shift traffic with the gateway's weighted routing.

## 3. Diagnosis while mitigating

- Compare the service configuration printed at start-up before and after the release. Changes to timeouts, pool sizes and retry settings are the most common cause.
- Check whether the failing requests share one statement, endpoint or downstream call.
- Look at the release notes and the change summary for library upgrades: a client library upgrade can change default timeouts even when the change summary does not mention them.

## 4. Verify recovery

- payments-service error rate back below 1% for 10 consecutive minutes.
- `DB_TIMEOUT` errors stop within 2 minutes of the rollback completing.
- Complaint volume on fund transfers returns to its normal level within 30 minutes.

## 5. Follow-up

- Raise a defect against the release and block redeployment until the configuration difference is understood.
- Add a pre-release check comparing effective client timeouts with the previous version.
- Communicate with customers following RB-GEN-003.

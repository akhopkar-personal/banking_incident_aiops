---
doc_id: RB-GEN-001
title: Safe service restart
doc_type: runbook
services: all
last_verified: 2026-07-30
citation_status: verified
owner: production-support
---

# RB-GEN-001: Safe service restart

## 1. When a restart helps

A restart helps when one service instance is unhealthy on its own: a memory leak, a stuck thread pool, a lost connection that does not recover. It does not help when a dependency is failing, when a release or configuration change caused the problem, or during a Kafka consumer rebalance storm (a restart starts another rebalance).

## 2. Before restarting

1. Check that the other instances of the service are healthy and can take the traffic.
2. Check recent changes to the service. If a change caused the problem, roll back instead (RB-PAY-003).
3. Capture a thread dump and the last 15 minutes of logs from the unhealthy instance.

## 3. Restart

1. Restart one instance at a time with the platform's rolling restart, never all instances at once.
2. Wait for the instance to pass its health check before restarting the next one.
3. A rolling restart is a low-risk action that Production Support may perform for Low and Medium incidents without escalation.

## 4. Verify

- Error rate and latency of the restarted instance match the other instances for 10 minutes.
- If the problem returns within an hour, escalate instead of restarting again.

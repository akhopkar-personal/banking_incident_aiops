---
doc_id: RB-KFK-001
title: Consumer lag and rebalance storms on the EventHub platform
doc_type: runbook
services: kafka-platform, ledger-consumer, notification-service
last_verified: 2026-08-25
citation_status: verified
owner: eventhub-platform
---

# RB-KFK-001: Consumer lag and rebalance storms on the EventHub platform

## 1. When to use this runbook

Use this runbook when consumers of payments.transactions fall behind. Typical signs:

- Consumer lag on payments.transactions above 10,000 messages and rising.
- Repeated consumer-group rebalances, `CommitFailedException` in consumer logs.
- Under-replicated partitions or a broker reported down.
- Customers report stale balances or missing transaction alerts, while payment APIs succeed.

## 2. Assess the broker side first

1. Check cluster health: brokers online, controller quorum healthy, offline partitions. Offline partitions on a payments topic make this an S1: follow section 5 immediately.
2. Check under-replicated partitions. A count that appears at the same time as a broker failure points to the broker, not to the consumers.
3. Read the failed broker's last log lines (disk, memory, network). Do not restart a broker with a disk error; replace the disk or the node first.

## 3. Stabilise the consumers

1. Rebalances during a broker failure are expected. Do not restart consumers while rebalances are still happening: each restart starts another rebalance.
2. If rebalances keep repeating after partition leadership has settled, increase the consumer session timeout or enable static group membership, following the consumer team's configuration change process.
3. Scale out consumer instances only up to the partition count (12 for payments.transactions); more instances stay idle.
4. Resetting consumer offsets is a high-risk action. It is never the first step, needs two-step confirmation, and can skip or replay payments: use it only with the ledger team and RB-PAY-005 reconciliation in place.

## 4. Verify recovery

- No rebalances for 10 minutes.
- Lag falling steadily; estimate catch-up time from the lag and the consumption rate.
- Under-replicated partitions back to 0 after the broker returns.
- Balance updates and notifications delivered for new events.

## 5. Escalation

- Offline partitions, or controller quorum lost: page the EventHub platform on-call immediately (S1).
- Lag still rising 30 minutes after the broker side is stable: escalate to the consuming teams (ledger, notifications).
- Customer communication about delayed balances and alerts follows RB-GEN-003.

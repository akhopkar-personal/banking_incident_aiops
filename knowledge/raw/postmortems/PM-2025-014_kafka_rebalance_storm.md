---
doc_id: PM-2025-014
title: "Postmortem: rebalance storm on ledger-consumer after a broker rolling restart"
doc_type: postmortem
services: kafka-platform, ledger-consumer
incident_date: 2025-06-19
severity: S2
last_verified: 2026-07-22
citation_status: verified
owner: eventhub-platform
---

# PM-2025-014: rebalance storm on ledger-consumer after a broker rolling restart

## 1. Summary

On 19 June 2025 a planned rolling restart of the Kafka brokers triggered repeated consumer-group rebalances in ledger-consumer. Each rebalance paused consumption for 20 to 40 seconds, and lag on payments.transactions grew to about 40,000 messages. Customers saw stale balances for up to 35 minutes. Payments themselves were accepted normally.

## 2. Impact

- Balance updates delayed by up to 35 minutes.
- Transaction alerts (SMS and push) delayed by up to 40 minutes.
- 58 complaints about missing alerts and wrong balances.

## 3. Timeline (UTC)

- 21:00 Rolling restart of the brokers started (planned maintenance).
- 21:04 First ledger-consumer rebalance; `CommitFailedException` errors began.
- 21:15 Lag alert on payments.transactions above 10,000 messages.
- 21:22 Consumers restarted by the on-call engineer; this caused two further rebalances.
- 21:40 Session timeout raised from 10 s to 45 s; rebalances stopped.
- 22:15 Lag back to normal.

## 4. Root cause

The consumer session timeout (10 s) was shorter than the time a consumer needed to rejoin while partition leadership moved during the restart. Members were expelled and rejoined repeatedly. Restarting the consumers made it worse.

## 5. Actions

- Session timeout raised to 45 s and static group membership enabled for ledger-consumer.
- Runbook RB-KFK-001 updated: do not restart consumers during a rebalance storm.
- Broker maintenance now announced to consuming teams 24 hours in advance.

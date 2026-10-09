---
doc_id: SVC-011
title: Service documentation for kafka-platform
doc_type: service_doc
services: kafka-platform
last_verified: 2026-08-20
citation_status: verified
owner: eventhub-platform
---

# SVC-011: kafka-platform

## 1. Purpose

The EventHub platform: a 3-broker Kafka cluster in KRaft mode, Kafka Connect, ACLs and the Schema Registry. Carries `payments.transactions` (12 partitions, replication factor 3).

## 2. Dependencies

- Depends on: none (no upstream service dependencies).
- Used by: ledger-consumer, notification-service, payments-service.
- Customer-facing: no.

## 3. Interfaces

- Topic `payments.transactions`
- Kafka Connect connectors `ledger-sink-jdbc` and `notification-sms-sink`
- Schema Registry subjects such as `payments.transactions-value`

## 4. Normal behaviour

- Under-replicated partitions 0, offline partitions 0, all 3 replicas in sync.

## 5. Common failure modes

- Broker failure: under-replicated partitions, consumer rebalances and lag (RB-KFK-001).
- Offline partitions on payments topics stop payments being published: S1.
- Schema compatibility warnings on unrelated subjects do not affect payments.transactions.
- ACL changes can deny a consumer or producer access to a topic.

## 6. Ownership

Owner team: eventhub-platform. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

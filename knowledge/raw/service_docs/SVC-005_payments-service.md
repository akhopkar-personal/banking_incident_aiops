---
doc_id: SVC-005
title: Service documentation for payments-service
doc_type: service_doc
services: payments-service
last_verified: 2026-08-20
citation_status: verified
owner: payments
---

# SVC-005: payments-service

## 1. Purpose

Accepts customer payments (transfers, card payments, bill payments), records them in core-banking-db, sends them to the payment switch through payment-network-gateway, and publishes every payment to the `payments.transactions` topic.

## 2. Dependencies

- Depends on: core-banking-db, payment-network-gateway, kafka-platform.
- Used by: api-gateway.
- Customer-facing: yes.

## 3. Interfaces

- `/payments/v1/payments`: payment submission
- Produces to Kafka topic `payments.transactions` (12 partitions)

## 4. Normal behaviour

- `/payments/v1/payments`: about 600 requests per minute, p95 latency about 260 ms, error rate about 0.5%.

## 5. Common failure modes

- Release or configuration regressions, for example changed DB client timeouts (RB-PAY-003, PM-2025-011).
- Retries without an idempotency key create duplicate payments (RB-PAY-005).
- Failures on one payment route come from payment-network-gateway (RB-NET-004).
- DB saturation makes payments slow or fail (RB-DB-002).

## 6. Ownership

Owner team: payments. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

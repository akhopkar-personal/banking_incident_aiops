---
doc_id: SVC-007
title: Service documentation for notification-service
doc_type: service_doc
services: notification-service
last_verified: 2026-08-20
citation_status: verified
owner: notifications
---

# SVC-007: notification-service

## 1. Purpose

Sends SMS, push and email alerts for transactions. Consumes `payments.transactions` as consumer group `notification-cg`.

## 2. Dependencies

- Depends on: kafka-platform.
- Used by: customer channels directly.
- Customer-facing: no.

## 3. Interfaces

- `/notify/v1/send`: outbound notifications
- Consumes `payments.transactions` (group `notification-cg`)

## 4. Normal behaviour

- `/notify/v1/send`: about 500 requests per minute, p95 latency about 90 ms, error rate about 0.2%.
- Consumer lag on payments.transactions normally below 500 messages.

## 5. Common failure modes

- Alert delays when consumer lag grows on payments.transactions (RB-KFK-001).
- Slow third-party SMS or email providers; these delay alerts but do not affect payments.

## 6. Ownership

Owner team: notifications. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

---
doc_id: SVC-010
title: Service documentation for payment-network-gateway
doc_type: service_doc
services: payment-network-gateway
last_verified: 2026-08-20
citation_status: verified
owner: payments-network
---

# SVC-010: payment-network-gateway

## 1. Purpose

Connects payments-service to the external payment switch over three routes: card, UPI and NEFT. Each route uses its own TLS client certificate.

## 2. Dependencies

- Depends on: none (no upstream service dependencies).
- Used by: payments-service.
- Customer-facing: no.

## 3. Interfaces

- `/routes/card`, `/routes/upi`, `/routes/neft`: one endpoint per route
- TLS connections to `external-switch`

## 4. Normal behaviour

- `/routes/card`: about 180 requests per minute, p95 latency about 340 ms, error rate about 0.4%.
- `/routes/upi`: about 220 requests per minute, p95 latency about 300 ms, error rate about 0.4%.
- `/routes/neft`: about 60 requests per minute, p95 latency about 450 ms, error rate about 0.4%.

## 5. Common failure modes

- Certificate expiry on one route: that route fails completely while the others work (RB-NET-004).
- Slow responses from the switch cause timeouts and retries in payments-service.

## 6. Ownership

Owner team: payments-network. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

---
doc_id: SVC-003
title: Service documentation for api-gateway
doc_type: service_doc
services: api-gateway
last_verified: 2026-08-20
citation_status: verified
owner: platform-gateway
---

# SVC-003: api-gateway

## 1. Purpose

Single entry point for customer channels. Routes requests to auth-service, payments-service and accounts-service, and applies rate limits and load-shedding profiles (RB-GEN-002).

## 2. Dependencies

- Depends on: auth-service, payments-service, accounts-service.
- Used by: mobile-app, net-banking.
- Customer-facing: yes.

## 3. Interfaces

- `/gw/v1/route`: all routed customer traffic
- Mutual TLS to auth-service (route `internal-mtls`)

## 4. Normal behaviour

- `/gw/v1/route`: about 2600 requests per minute, p95 latency about 180 ms, error rate about 0.3%.

## 5. Common failure modes

- 5xx responses from one upstream service: look at that service (for example RB-PAY-003 for payments-service).
- mTLS certificate expiry to auth-service blocks all logins (PM-2025-023).
- Latency rises when an upstream service is slow; the gateway itself is rarely the cause.

## 6. Ownership

Owner team: platform-gateway. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

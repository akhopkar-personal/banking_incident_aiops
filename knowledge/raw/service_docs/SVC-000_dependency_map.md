---
doc_id: SVC-000
title: Service dependency map of the bank's digital platform
doc_type: service_doc
services: all
last_verified: 2026-08-20
citation_status: verified
owner: architecture
---

# SVC-000: Service dependency map

## 1. Overview

Customers reach the bank through the mobile app, net banking and the call centre. The app and net banking call api-gateway, which routes to auth-service, payments-service and accounts-service. The call centre uses its own tools and is not instrumented here, but it is the main source of customer complaints.

## 2. Dependencies

- mobile-app and net-banking depend on api-gateway.
- api-gateway depends on auth-service, payments-service and accounts-service.
- payments-service depends on core-banking-db, payment-network-gateway and kafka-platform (it publishes to payments.transactions).
- accounts-service depends on core-banking-db.
- ledger-consumer depends on kafka-platform (it consumes payments.transactions) and core-banking-db.
- notification-service depends on kafka-platform (it consumes payments.transactions).
- payment-network-gateway connects to the external payment switch.

## 3. Blast radius

- core-banking-db: affects accounts-service, payments-service, ledger-consumer, and through them auth, api-gateway and both customer channels. A saturated database shows up as slow logins and balance checks across channels.
- kafka-platform: affects ledger-consumer (stale balances) and notification-service (late alerts). Payments are still accepted unless partitions go offline.
- payment-network-gateway: affects payments only, and often only one route (card, UPI or NEFT).
- api-gateway: affects every customer journey on the app and net banking.

## 4. Customer-facing services

mobile-app, net-banking, api-gateway, auth-service, payments-service and accounts-service are customer-facing. Their error rate and latency drive the severity rules. notification-service, ledger-consumer, core-banking-db, payment-network-gateway and kafka-platform are internal: their failures matter through the customer-facing services they affect.

## 5. Machine-readable version

The same map is in data/service_dependencies.json and is returned by the get_service_dependencies tool.

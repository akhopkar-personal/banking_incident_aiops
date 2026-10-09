---
doc_id: SVC-001
title: Service documentation for mobile-app
doc_type: service_doc
services: mobile-app
last_verified: 2026-08-20
citation_status: verified
owner: mobile-channels
---

# SVC-001: mobile-app

## 1. Purpose

Backend for the customer mobile app (iOS and Android): sessions, balances, transfers and card payments, all through api-gateway.

## 2. Dependencies

- Depends on: api-gateway.
- Used by: customer channels directly.
- Customer-facing: yes.

## 3. Interfaces

- `/app/v1/session`: app session and screen data

## 4. Normal behaviour

- `/app/v1/session`: about 1200 requests per minute, p95 latency about 420 ms, error rate about 0.4%.

## 5. Common failure modes

- Errors or latency in api-gateway or the services behind it show up here first; check those before the app backend.
- Static images come from a third-party CDN; a CDN outage is cosmetic only (PM-2025-031).
- App releases change only the app and its backend; they do not touch core-banking-db.

## 6. Ownership

Owner team: mobile-channels. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

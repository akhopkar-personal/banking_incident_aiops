---
doc_id: SVC-002
title: Service documentation for net-banking
doc_type: service_doc
services: net-banking
last_verified: 2026-08-20
citation_status: verified
owner: web-channels
---

# SVC-002: net-banking

## 1. Purpose

Backend for the web banking site. Same journeys as the mobile app, through api-gateway.

## 2. Dependencies

- Depends on: api-gateway.
- Used by: customer channels directly.
- Customer-facing: yes.

## 3. Interfaces

- `/web/v1/session`: web session and page data

## 4. Normal behaviour

- `/web/v1/session`: about 800 requests per minute, p95 latency about 380 ms, error rate about 0.4%.

## 5. Common failure modes

- Like mobile-app, it reflects problems in api-gateway and the services behind it.
- Login slowness usually comes from auth-service or core-banking-db, not from net-banking itself.

## 6. Ownership

Owner team: web-channels. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

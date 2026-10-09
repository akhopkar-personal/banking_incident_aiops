---
doc_id: SVC-004
title: Service documentation for auth-service
doc_type: service_doc
services: auth-service
last_verified: 2026-08-20
citation_status: verified
owner: identity
---

# SVC-004: auth-service

## 1. Purpose

Customer login, one-time passcodes and token refresh. Reads customer profiles from core-banking-db.

## 2. Dependencies

- Depends on: none (no upstream service dependencies).
- Used by: api-gateway.
- Customer-facing: yes.

## 3. Interfaces

- `/auth/v1/login`: login and token issue

## 4. Normal behaviour

- `/auth/v1/login`: about 900 requests per minute, p95 latency about 120 ms, error rate about 0.2%.

## 5. Common failure modes

- Slow profile reads when core-banking-db is saturated (RB-DB-002).
- Certificate problems on the gateway's mTLS route (RB-NET-004, PM-2025-023).

## 6. Ownership

Owner team: identity. Major incidents page the team's on-call; Low and Medium incidents go to the production-support queue.

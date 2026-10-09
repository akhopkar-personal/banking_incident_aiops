---
doc_id: RB-GEN-002
title: Traffic throttling and load shedding
doc_type: runbook
services: api-gateway
last_verified: 2025-11-20
citation_status: verified
owner: platform-gateway
---

# RB-GEN-002: Traffic throttling and load shedding

## 1. When to throttle

Throttle traffic when a shared dependency is overloaded and more requests make it worse, for example core-banking-db saturation or a slow payment switch. Throttling protects the most important journeys (payments, login) at the cost of less important ones.

## 2. Priorities

Shed load in this order, stopping as soon as the dependency recovers:

1. Background and reporting calls (statements, analytics, marketing).
2. Non-essential app features (spending insights, offers).
3. Balance refreshes, which are reduced to one per session per minute.
4. Payments and login are never throttled below 80% of normal volume without the incident commander's approval.

## 3. Apply the throttle

1. Use the gateway's rate-limit profiles (profile names: shed-1, shed-2, shed-3). Each profile applies the steps above up to that level.
2. Throttling traffic is a medium-risk action and needs approval from the incident commander.
3. Announce the throttle in the incident channel, with the profile and the time.

## 4. Remove the throttle

Step back one profile at a time, waiting 10 minutes between steps, while watching the dependency's health.

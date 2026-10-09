---
doc_id: RB-GEN-003
title: Incident communication protocol
doc_type: runbook
services: all
last_verified: 2026-08-12
citation_status: verified
owner: incident-management
---

# RB-GEN-003: Incident communication protocol

## 1. Audiences by severity

- Major (S1, S2): incident chat channel, the owning team's channel, and email to the stakeholders listed for the service. The incident manager owns communication.
- Low and Medium (S3, S4): the owning team's channel only.

## 2. Timing

- Major incidents: first internal message within 15 minutes of the page, then an update at least every 30 minutes until resolved.
- Customer-facing status for Major incidents within 30 minutes (REG-002).
- A final message when the incident is resolved, with the time of recovery.

## 3. Content of every message

1. What customers are experiencing, in plain language.
2. Since when, in UTC.
3. What is being done, and the next update time.
4. Only numbers that are confirmed by monitoring or the incident record.

## 4. Tone rules

- Do not blame a person or a team.
- Do not guess the root cause in customer-facing messages; say that it is under investigation until it is confirmed.
- Do not promise a recovery time unless the fix is already in progress and its duration is known.

## 5. After the incident

The incident manager sends a summary to stakeholders within one business day, and the postmortem follows the blameless template.

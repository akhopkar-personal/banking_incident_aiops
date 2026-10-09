---
doc_id: PM-2025-023
title: "Postmortem: internal mTLS certificate expiry between api-gateway and auth-service"
doc_type: postmortem
services: api-gateway, auth-service
incident_date: 2025-09-03
severity: S1
last_verified: 2026-08-10
citation_status: verified
owner: platform-gateway
---

# PM-2025-023: internal mTLS certificate expiry between api-gateway and auth-service

## 1. Summary

On 3 September 2025 at 06:00 UTC the client certificate that api-gateway uses for mutual TLS to auth-service expired. All new logins failed for 41 minutes. Customers who were already logged in could continue until their session needed a token refresh.

## 2. Impact

- 100% of new logins failed between 06:00 and 06:41 UTC.
- 310 complaints about being unable to log in.
- No data exposure: the connection failed closed.

## 3. Timeline (UTC)

- Previous 14 days: certificate expiry warnings logged daily by api-gateway, but not alerted.
- 06:00 Certificate expired; handshakes to auth-service failed.
- 06:03 Login failure alert.
- 06:15 Handshake errors with "certificate has expired" found in gateway logs.
- 06:30 Emergency certificate issued by the internal CA.
- 06:41 Certificate deployed and gateway routes reloaded; logins recovered.

## 4. Root cause

The certificate was issued manually two years earlier and was not in the certificate inventory, so the expiry monitor did not cover it. The log warnings existed but nobody was alerted on them.

## 5. Actions

- All gateway and payment-route certificates added to the inventory and the expiry monitor (alerts at 30, 14 and 3 days).
- Certificate expiry warnings in logs now create a ticket automatically.
- Option to disable certificate verification during an emergency was explicitly rejected.

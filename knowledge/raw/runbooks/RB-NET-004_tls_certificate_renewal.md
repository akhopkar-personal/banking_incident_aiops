---
doc_id: RB-NET-004
title: TLS certificate expiry and renewal on payment routes
doc_type: runbook
services: payment-network-gateway, payments-service
last_verified: 2026-08-30
citation_status: verified
owner: payments-network
---

# RB-NET-004: TLS certificate expiry and renewal on payment routes

## 1. When to use this runbook

Use this runbook when one payment route to the external payment switch fails while other routes work. Typical signs:

- One route (card, UPI or NEFT) at or near 100% failure; the other routes normal.
- Network events show TLS handshake failures or `tls_status: expired` on that route.
- payment-network-gateway logs `CERT_EXPIRED` or `CERT_EXPIRY_SOON` warnings.
- Internal services and core-banking-db healthy.

## 2. Confirm the certificate

1. Read the certificate's notAfter date from the gateway logs or the network events (`cert_expiry`).
2. Check whether the failure started at that time. A failure that starts exactly at the expiry time confirms the cause.
3. Check whether the switch rotated its own certificate or CA at the same time; if so, the fix is on the trust store, not the client certificate.

## 3. Renew and deploy the certificate

1. Request an emergency certificate from the internal CA for the route's client identity. Emergency issuance takes about 15 minutes.
2. Deploy the new certificate to the gateway's secret store and reload the route. A reload does not interrupt the other routes.
3. Renewing the certificate is a medium-risk action: get approval from the incident commander.
4. Never disable TLS or certificate verification on a payment route, not even temporarily. This is prohibited by policy.

## 4. Reduce customer impact meanwhile

- If the switch offers a backup endpoint with a valid certificate, fail the route over to it (medium risk, needs approval).
- Show a message in the app that card payments are temporarily unavailable, following RB-GEN-003.

## 5. Verify recovery and follow-up

- Handshakes succeed and the route's failure rate is below 1% for 10 minutes.
- Add the certificate to the expiry monitor, with alerts at 30, 14 and 3 days before expiry.
- Find out why the `CERT_EXPIRY_SOON` warnings were not acted on.

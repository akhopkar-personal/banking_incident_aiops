---
doc_id: PM-2025-031
title: "Postmortem: missing images in the mobile app after a CDN outage"
doc_type: postmortem
services: mobile-app
incident_date: 2025-11-27
severity: S4
last_verified: 2026-07-08
citation_status: verified
owner: mobile-channels
---

# PM-2025-031: missing images in the mobile app after a CDN outage

## 1. Summary

On 27 November 2025 the third-party content delivery network that serves the mobile app's static images had a regional outage for 70 minutes. Icons and promotional banners did not load. All banking functions worked normally because they do not depend on the CDN.

## 2. Impact

- Cosmetic only: missing icons and banners.
- 12 complaints and some social media comments about the app "looking broken".
- No effect on payments, login, balances or alerts.

## 3. Timeline (UTC)

- 09:10 CDN provider's regional outage began.
- 09:25 Social media reports of missing images.
- 09:40 Mobile team confirmed the CDN outage on the provider's status page.
- 10:20 CDN recovered; images loaded again after the app's cache refresh.

## 4. Root cause

An outage at the CDN provider. The app had no fallback for static assets.

## 5. Actions

- The app now bundles the most important icons so that core screens look normal without the CDN.
- CDN health added to the mobile team's dashboard. No change needed to backend services.

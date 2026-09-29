# PinkBakes Step 11 — Comprehensive Testing / QA Report

**Date:** 2026-09-28 (Asia/Calcutta)  
**Mode:** TEST/STABILIZE — no rebuild of app features  
**Machine:** DESKTOP-9CS7TKU (`7fba3766-eefc-46e0-868c-941910a59e67`)

## Environment

| Item | Value |
|------|-------|
| Python | 3.10.6 |
| Django | 5.2.16 |
| DRF / razorpay | installed (requirements.txt) |
| DB for tests | SQLite in-memory (`manage.py test`) |
| Razorpay | DEBUG defaults / TEST secret only (`PAYMENT_ENABLED` false; local deterministic gateway ids) |
| Frontend | Vite 6 + React 18 (`pinksBakes`) |
| CI | None (no `.github` workflows in BE or FE) |
| FE test/lint | No `test` / `lint` / `typecheck` scripts; no Playwright/Vitest |

## Baseline (Phase 1 — before new tests)

| Suite | Result |
|-------|--------|
| `python manage.py test accounts catalog notifications seo` | **125 ran, 0 failed, 0 errors, 0 skipped** (~244s) |
| `npm run build` | **PASS** (~5.8s) |
| Pre-existing failures | **None** |

Covered already before this step: OTP/reset (partial), cart validate, coupons + limits + concurrency stub, inventory reserve/oversell + concurrency, order status transitions, Razorpay verify/webhook invalid/duplicate, refunds idempotency, IDOR (orders/addresses/payments), admin blocked for customers, delivery auth, SEO, notifications.

## Final counts (after expansions)

| Suite | Result |
|-------|--------|
| Django full suite | **142 ran, 0 failed, 0 errors, 0 skipped** (~220s) |
| Delta | **+17 tests** |
| `npm run build` (re-run) | **PASS** (~5.5s) |

## Build / Lint

- Backend: Django system check — no issues.
- Frontend: production build OK; **no eslint/tsc/npm test** configured (document as unavailable).
- Browser E2E: **unavailable** (no Playwright/Cypress harness). Journeys covered via Django APIClient instead.

## Functional areas status

| Area | Status | Notes |
|------|--------|-------|
| Auth OTP / reset / signin / me | PASS | Lockout @5 attempts, resend throttle, signin unverified blocked |
| Cart validate / empty / zero qty | PASS | |
| Coupons limits / concurrency | PASS | SQLite threaded HTTP limited (documented in test) |
| Inventory reservation / oversell | PASS | Including last-unit race |
| Orders status / cancel / IDOR / tracking | PASS | Tracking IDOR added |
| Razorpay verify / webhook / duplicate | PASS | Sandbox/test secrets only |
| Refunds idempotency | PASS | |
| Admin blocked for customers / delivery employee | PASS | |
| Delivery quote / zones / address IDOR | PASS | |
| Notifications authz / idempotency | PASS | Foreign mark-read → 404 |
| SEO robots/sitemap/JSON-LD | PASS | |
| Cache freshness (price/stock vs payment) | PASS | List/categories invalidate; payment uses live price/stock |
| FE smoke | PASS (build only) | No unit/E2E harness |

## Bugs unresolved

**Critical / High:** none found; no production code changes required this step.

| Severity | Area | Problem | Impact | Reason not fixed | Next |
|----------|------|---------|--------|------------------|------|
| Medium | FE QA | No unit/E2E/lint/typecheck scripts | Regressions in SPA only caught by build transpile | Out of scope to invent full Playwright this step | Add Vitest smoke + optional Playwright for checkout |
| Medium | CI | No GitHub Actions / other CI | Suites only run manually | No existing CI to extend | Add workflow: `manage.py test` + `npm run build` |
| Low | Cache | Admin dashboard KPI cache relies on short TTL (locmem cannot SCAN-delete) | KPI can lag ≤20s after mutations | By design (PERFORMANCE.md) | Redis + key delete when scaling |
| Low | Concurrency | Coupon global-limit threaded test is SQLite-aware stub | True multi-thread HTTP races not exercised on SQLite | Documented limitation | Re-run with Postgres in staging |
| Low | Tooling | `accounts/test_security.py` had UTF-8 BOM (tests still ran) | Cosmetic / some AST tools fail | Non-blocking | Save as UTF-8 without BOM |

## Files changed

- `accounts/tests.py` — SigninAndMeTests, OtpLockoutAndThrottleTests (+7)
- `accounts/test_security.py` — TrackingAndNotificationIdorTests (+3)
- `catalog/tests.py` — CartEdgeCaseTests, CacheFreshnessRegressionTests (+7)
- `QA_STEP11_REPORT.md` — this report

**Not touched:** `media/products`, product `image` fields, production secrets, app feature rebuilds.

## Tests added (17)

1. signin token for verified user  
2. signin bad password  
3. signin unverified rejected  
4. me requires auth + profile  
5. login OTP lockout after 5 failures  
6. verify-otp lockout after 5 failures  
7. send-verification 1-minute throttle  
8. tracking IDOR forbidden  
9. tracking owner allowed  
10. notification mark-read IDOR  
11. empty cart validate  
12. zero quantity rejected  
13. payment create empty cart  
14. product list cache invalidates on price change  
15. categories cache invalidates on new product  
16. payment create uses live price after change  
17. payment create rejects oversell after stock drop  

## Commands executed

```powershell
cd E:\repos\pinkbakes_backend
$env:DJANGO_DEBUG='true'
python manage.py test accounts catalog notifications seo --verbosity=2   # baseline 125 OK
python manage.py test accounts catalog notifications seo --verbosity=1   # final 142 OK

cd E:\repos\pinksBakes
npm run build   # baseline + final PASS
```

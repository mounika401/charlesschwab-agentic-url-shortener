# Design: click analytics + BUG-101 (brownfield)

## Impact analysis (from the codebase agent)

The redirect path is `app.redirect -> LinkService.resolve -> LinkRepository.increment_clicks`.
BUG-101 lives in `LinkService.resolve`: the counter is incremented *before* the
expiry check, so a 410 response still counts a click. Analytics hangs off the
same call, so the fix must land before analytics is wired, otherwise the new
events inherit the bug.

Blast radius: `link_service` is imported by `app`; `db` by `app` and
`repository`. Existing tests covering those modules are re-run by every task
gate; the full suite runs in `verify`.

## Changes

1. **Schema v2** (`db.py`): migration 2 adds `click_events` (append-only) with
   `ON DELETE CASCADE` to `links` and a `(code, occurred_at)` index. Additive and
   transactional, so v1 databases upgrade in place and restarts are no-ops.
2. **Analytics module** (`analytics.py`): `ClickContext` (what HTTP knows),
   `visitor_hash` (salted, truncated SHA-256), `referrer_host`,
   `AnalyticsRepository.record/stats`. Salt comes from
   `SHORTENER_ANALYTICS_SALT`; if unset a random per-process salt is used
   (safe default: unique counts reset on restart, nothing leaks).
3. **BUG-101 fix** (`link_service.py`): check expiry, *then* increment and record.
   A regression test is written first and must fail against the old code.
4. **Stats API** (`app.py`, `schemas.py`): `GET /api/v1/links/{code}/stats`.
   Existing responses unchanged.

## Backward compatibility

No existing field, status code or route changes. `LinkService` gains an optional
`analytics` collaborator, so existing constructors keep working.

# Design: URL shortener v1 (greenfield)

## Context

Internal service, single node, must run on a laptop, must survive restarts.
Throughput target (100 rps, p99 < 50 ms for redirects) is modest; the dominant
risks are correctness (lost click updates, expired links still resolving) and
abuse of the redirect (script/file URLs, guessable codes).

## Architecture

```
HTTP (FastAPI app.py) -> LinkService (business rules) -> LinkRepository (SQL) -> Database (SQLite + migrations)
                               |-> codegen (CSPRNG base62)   |-> validation (URL rules)
```

* **Transport layer** maps requests/responses and domain errors to status codes
  in exactly one place (`ERROR_STATUS`).
* **Service layer** owns every rule: URL validation, alias rules, bounded retry
  on code collisions, expiry.
* **Repository layer** is pure data access; the click counter is incremented
  with a single `UPDATE ... SET n = n + 1` so concurrent redirects never lose
  updates.
* **Database** uses forward-only, versioned migrations recorded in
  `schema_version`, applied at start-up and safe to re-run.

## Failure modes considered

| Failure | Handling |
|---|---|
| Random code collides | Retry up to 5 times, then 503 (`CodeSpaceExhaustedError`) signalling the code length is too small |
| Concurrent redirects | Atomic SQL increment; SQLite serialises writers; RLock around the shared connection |
| Crash mid-migration | Each migration runs in its own transaction; version row written in the same transaction |
| Expired link | 410 Gone, distinguishable from unknown (404) |

## API contract

| Method | Path | Success | Notes |
|---|---|---|---|
| POST | `/api/v1/links` | 201 | create; 409 alias taken; 422 invalid |
| GET | `/api/v1/links/{code}` | 200 | metadata; 404 unknown |
| DELETE | `/api/v1/links/{code}` | 204 | hard delete |
| GET | `/{code}` | 307 | redirect; 404 unknown; 410 expired |
| GET | `/healthz` | 200 | liveness |
| GET | `/readyz` | 200 | readiness + schema version |

## Data model changes

- **links**: new: code PK, url, created_at (indexed), expires_at, click_count

## Decisions

- [ADR-001](../adr/ADR-001.md) SQLite behind a repository interface
- [ADR-002](../adr/ADR-002.md) Random base62 codes from a CSPRNG
- [ADR-003](../adr/ADR-003.md) 307 Temporary Redirect

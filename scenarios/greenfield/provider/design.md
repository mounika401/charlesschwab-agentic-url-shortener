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

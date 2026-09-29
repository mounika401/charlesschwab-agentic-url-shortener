# Design: public-launch hardening (ambiguous brief)

## Interpretation

The brief ("safe for public use", "robust against abuse") was normalised via
clarification into two concrete controls with an explicit fail-mode: abuse
controls fail closed and never affect resolving existing links.

## Destination safety policy (`safety.py`)

`validate_url` answers *is this a well-formed web URL*; the new
`UrlSafetyPolicy.check` answers *may we publish it behind our domain*. Rules:

| Rule | Example rejected | Why |
|---|---|---|
| Embedded credentials | `https://trusted.com@evil.com/` | Reads as trusted.com to humans |
| Internal hostnames | `localhost`, `*.local`, `*.internal` | Internal targets / SSRF chains |
| Non-global IP literals | `10.0.0.5`, `169.254.169.254`, `[::1]` | Cloud metadata, private ranges |
| Own domain | `https://sho.rt/abc` | Redirect loops, laundering |
| Operator blocklist | `*.evil.test` | Label-boundary suffix match, so `notevil.test` is allowed |

Checks are syntactic. DNS-based attacks (a public name resolving to a private IP)
remain a documented residual risk because resolving names in the request path
would add latency and let clients make the service issue network requests.

## Rate limiting (`ratelimit.py`)

Token bucket per client key (direct peer address - trusting `X-Forwarded-For`
would let clients pick their own bucket). Burst = capacity, refill =
capacity/60s. Memory bounded by LRU eviction of idle keys. State is
per-process: with N replicas the effective limit is N x capacity (accepted at
clarification; a Redis-backed limiter can implement the same `acquire` API).

* Creation: 30/min per client (`SHORTENER_CREATE_RATE_LIMIT`, 0 disables).
* Redirects: 300/min per client (`SHORTENER_REDIRECT_RATE_LIMIT`, 0 disables).
  Enforced *before* resolve, so throttled requests never count as clicks
  (added after the release-review change request).

Exceeding a limit returns `429 RateLimitedError` with `Retry-After` (whole
seconds, >= 1).

## API contract

| Method | Path | Success | Notes |
|---|---|---|---|
| POST | `/api/v1/links` | 201 | 422 UnsafeUrlError; 429 RateLimitedError + Retry-After |
| GET | `/api/v1/links/{code}` | 200 | unchanged |
| DELETE | `/api/v1/links/{code}` | 204 | unchanged |
| GET | `/api/v1/links/{code}/stats` | 200 | unchanged |
| GET | `/{code}` | 307 | unchanged |
| GET | `/{code}` | 307 | 429 when the per-client redirect limit is exceeded |
| GET | `/healthz` | 200 | unchanged |
| GET | `/readyz` | 200 | unchanged |

## Decisions

- [ADR-007](../adr/ADR-007.md) Syntactic destination safety policy
- [ADR-008](../adr/ADR-008.md) In-process token-bucket rate limiting keyed by peer address
- [ADR-009](../adr/ADR-009.md) Rate-limit redirects before resolving

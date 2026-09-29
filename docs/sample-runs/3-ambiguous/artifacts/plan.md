# Implementation plan (ambiguous)

Configuration and error types first (everything else refers to them); the rate limiter has no dependencies and runs in parallel with that. The safety policy is enforced in the service layer (so every caller gets it, not only HTTP), and the HTTP layer wires both controls last. Redirect limiting exists only if the spec carries the redirect_rate_limit flag.


| Task | Title | Depends on | Scope | Risk | Tests |
|---|---|---|---|---|---|
| A1 | Abuse-control settings and error types | - | shortener/config.py, shortener/errors.py, tests/shortener/test_config_models.py | low | tests/shortener/test_config_models.py |
| A3 | Token-bucket rate limiter | - | shortener/ratelimit.py, tests/shortener/test_ratelimit.py | low | tests/shortener/test_ratelimit.py |
| A2 | Destination safety policy | A1 | shortener/safety.py, tests/shortener/test_safety.py | medium | tests/shortener/test_safety.py |
| A4 | Enforce destination safety in the service layer | A2 | shortener/link_service.py, tests/shortener/test_link_service.py | medium | tests/shortener/test_link_service.py |
| A5 | Wire creation rate limit and safety into the API (v1.2.0) | A3, A4 | shortener/app.py, shortener/__init__.py, tests/shortener/test_api_safety.py | medium | tests/shortener/test_api_safety.py, tests/shortener/test_api.py |
| A6 | Per-client redirect rate limit | A5 | shortener/app.py, shortener/config.py, tests/shortener/test_redirect_ratelimit.py | medium | tests/shortener/test_redirect_ratelimit.py, tests/shortener/test_stats_api.py |

Parallel waves: [['A1', 'A3'], ['A2'], ['A4'], ['A5'], ['A6']]
Critical path: A1 -> A2 -> A4 -> A5 -> A6

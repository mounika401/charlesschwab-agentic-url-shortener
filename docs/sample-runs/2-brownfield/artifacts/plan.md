# Implementation plan (brownfield)

Migration first (everything else reads the new table). The BUG-101 regression test and the analytics module are independent and run in parallel. The fix depends on both: it must make the regression test pass and route successful clicks into analytics. The HTTP endpoint comes last so the public surface only changes once the data behind it is correct.


| Task | Title | Depends on | Scope | Risk | Tests |
|---|---|---|---|---|---|
| B1 | Schema v2 migration (click_events) | - | shortener/db.py | high | tests/shortener/test_persistence.py |
| B2 | Regression test for BUG-101 (test-first) | B1 | tests/shortener/test_bug101_regression.py | low |  |
| B3 | Analytics module | B1 | shortener/analytics.py, shortener/config.py, tests/shortener/test_analytics.py | medium | tests/shortener/test_analytics.py |
| B4 | Fix BUG-101 and record clicks on successful redirects | B2, B3 | shortener/link_service.py | medium | tests/shortener/test_bug101_regression.py, tests/shortener/test_link_service.py |
| B5 | Stats endpoint and version 1.1.0 | B4 | shortener/app.py, shortener/schemas.py, shortener/__init__.py, tests/shortener/test_stats_api.py | medium | tests/shortener/test_stats_api.py, tests/shortener/test_api.py |

Parallel waves: [['B1'], ['B2', 'B3'], ['B4'], ['B5']]
Critical path: B1 -> B3 -> B4 -> B5

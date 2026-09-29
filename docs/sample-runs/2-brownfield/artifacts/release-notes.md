# Release notes: brownfield 1.1.0

Per-link click analytics without storing personal data, and clicks counted only on successful redirects

## Readiness checklist

- [x] all_tasks_implemented: 5 tasks
- [x] tests_green: 53 passed, 0 failed
- [x] api_contract: missing=[]
- [x] no_high_security_findings: {'high': 0, 'medium': 0, 'low': 0}
- [x] docs_generated: docs/API.md
- [x] version_bumped: 1.0.0 -> 1.1.0
- [x] no_rejected_checkpoints: 6 checkpoint decisions so far

## Residual risks (accepted, not blocking)

- T5

## Change summary

```
CHANGELOG.md                              | 10 ++++
 docs/API.md                               | 10 +++-
 docs/adr/ADR-004.md                       | 20 ++++++++
 docs/adr/ADR-005.md                       | 17 +++++++
 docs/adr/ADR-006.md                       | 15 ++++++
 docs/design/brownfield.md                 | 55 ++++++++++++++++++++
 docs/security/threat-model-brownfield.md  | 10 ++++
 shortener/__init__.py                     |  2 +-
 shortener/analytics.py                    | 85 +++++++++++++++++++++++++++++++
 shortener/app.py                          | 34 +++++++++++--
 shortener/config.py                       |  9 +++-
 shortener/db.py                           | 17 +++++++
 shortener/link_service.py                 | 27 ++++++++--
 shortener/schemas.py                      | 20 +++++++-
 tests/shortener/test_analytics.py         | 45 ++++++++++++++++
 tests/shortener/test_bug101_regression.py | 30 +++++++++++
 tests/shortener/test_stats_api.py         | 21 ++++++++
 17 files changed, 414 insertions(+), 13 deletions(-)
```

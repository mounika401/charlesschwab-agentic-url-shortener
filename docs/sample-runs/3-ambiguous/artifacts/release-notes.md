# Release notes: ambiguous 1.2.0

Harden the public-facing shortener against malicious destinations and automated abuse without changing behaviour for legitimate clients

## Readiness checklist

- [x] all_tasks_implemented: 6 tasks
- [x] tests_green: 88 passed, 0 failed
- [x] api_contract: missing=[]
- [x] no_high_security_findings: {'high': 0, 'medium': 0, 'low': 0}
- [x] docs_generated: docs/API.md
- [x] version_bumped: 1.1.0 -> 1.2.0
- [x] no_rejected_checkpoints: 8 checkpoint decisions so far

## Residual risks (accepted, not blocking)

- none

## Change summary

```
CHANGELOG.md                               | 11 +++++
 docs/API.md                                |  6 +--
 docs/adr/ADR-007.md                        | 20 +++++++++
 docs/adr/ADR-008.md                        | 21 +++++++++
 docs/adr/ADR-009.md                        | 16 +++++++
 docs/design/ambiguous.md                   | 59 +++++++++++++++++++++++++
 docs/security/threat-model-ambiguous.md    | 11 +++++
 shortener/__init__.py                      |  2 +-
 shortener/app.py                           | 61 +++++++++++++++++++++++---
 shortener/config.py                        | 15 +++++++
 shortener/errors.py                        | 10 +++++
 shortener/link_service.py                  |  4 ++
 shortener/ratelimit.py                     | 55 +++++++++++++++++++++++
 shortener/safety.py                        | 50 +++++++++++++++++++++
 tests/shortener/test_api_safety.py         | 38 ++++++++++++++++
 tests/shortener/test_config_models.py      |  8 ++++
 tests/shortener/test_link_service.py       |  8 ++++
 tests/shortener/test_ratelimit.py          | 70 ++++++++++++++++++++++++++++++
 tests/shortener/test_redirect_ratelimit.py | 32 ++++++++++++++
 tests/shortener/test_safety.py             | 44 +++++++++++++++++++
 20 files changed, 530 insertions(+), 11 deletions(-)
```

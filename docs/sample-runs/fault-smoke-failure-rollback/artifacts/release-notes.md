# Release notes: greenfield 1.0.0

HTTP service that creates, resolves, inspects and deletes short links, with optional vanity codes and expiry

## Readiness checklist

- [x] all_tasks_implemented: 6 tasks
- [x] tests_green: 44 passed, 0 failed
- [x] api_contract: missing=[]
- [x] no_high_security_findings: {'high': 0, 'medium': 0, 'low': 0}
- [x] docs_generated: docs/API.md
- [x] version_bumped: None -> 1.0.0
- [x] no_rejected_checkpoints: 5 checkpoint decisions so far

## Residual risks (accepted, not blocking)

- T4
- T5
- T6
- T7

## Change summary

```
CHANGELOG.md                             |  12 ++++
 docs/API.md                              |  47 ++++++++++++++
 docs/adr/ADR-001.md                      |  21 +++++++
 docs/adr/ADR-002.md                      |  16 +++++
 docs/adr/ADR-003.md                      |  16 +++++
 docs/design/greenfield.md                |  55 +++++++++++++++++
 docs/security/threat-model-greenfield.md |  13 ++++
 requirements.txt                         |   5 ++
 shortener/__init__.py                    |  10 +++
 shortener/__main__.py                    |  14 +++++
 shortener/app.py                         | 102 +++++++++++++++++++++++++++++++
 shortener/codegen.py                     |  27 ++++++++
 shortener/config.py                      |  42 +++++++++++++
 shortener/db.py                          |  89 +++++++++++++++++++++++++++
 shortener/errors.py                      |  29 +++++++++
 shortener/link_service.py                |  69 +++++++++++++++++++++
 shortener/models.py                      |  24 ++++++++
 shortener/repository.py                  |  59 ++++++++++++++++++
 shortener/schemas.py                     |  27 ++++++++
 shortener/validation.py                  |  33 ++++++++++
 tests/shortener/conftest.py              |  29 +++++++++
 tests/shortener/test_api.py              |  69 +++++++++++++++++++++
 tests/shortener/test_config_models.py    |  28 +++++++++
 tests/shortener/test_link_service.py     |  48 +++++++++++++++
 tests/shortener/test_persistence.py      |  35 +++++++++++
 tests/shortener/test_units.py            |  45 ++++++++++++++
 26 files changed, 964 insertions(+)
```

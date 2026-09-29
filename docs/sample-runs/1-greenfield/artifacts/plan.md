# Implementation plan (greenfield)

Build bottom-up along the dependency direction of the layered design so every task can be proven by its own tests the moment it lands: domain + config first, then the two leaf utilities and persistence in parallel, then the service layer, then HTTP. Dependencies are declared separately so their approval does not block code work.


| Task | Title | Depends on | Scope | Risk | Tests |
|---|---|---|---|---|---|
| G1 | Domain model, errors and configuration | - | shortener/__init__.py, shortener/__main__.py, shortener/config.py, shortener/errors.py, shortener/models.py, tests/shortener/test_config_models.py | low | tests/shortener/test_config_models.py |
| G6 | Declare runtime and test dependencies | - | requirements.txt | low |  |
| G2 | Code generation and URL validation | G1 | shortener/codegen.py, shortener/validation.py, tests/shortener/test_units.py | low | tests/shortener/test_units.py |
| G3 | Persistence layer and schema v1 | G1 | shortener/db.py, shortener/repository.py, tests/shortener/conftest.py, tests/shortener/test_persistence.py | medium | tests/shortener/test_persistence.py |
| G4 | Link service (business rules) | G2, G3 | shortener/link_service.py, tests/shortener/test_link_service.py | low | tests/shortener/test_link_service.py |
| G5 | HTTP API | G4 | shortener/app.py, shortener/schemas.py, tests/shortener/test_api.py | medium | tests/shortener/test_api.py |

Parallel waves: [['G1', 'G6'], ['G2', 'G3'], ['G4'], ['G5']]
Critical path: G1 -> G3 -> G4 -> G5

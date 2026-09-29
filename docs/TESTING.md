# Testing approach

```bash
python -m pytest                          # everything (~40 s)
python -m pytest tests/shortener          # product only (~1 s)
python -m pytest tests/orchestrator -k "not scenarios"   # orchestrator unit + engine (~4 s)
python -m pytest tests/orchestrator/test_scenarios.py    # end-to-end pipeline runs (~35 s)
```

## Layers

| Layer | Location | What it proves |
|---|---|---|
| Product unit | `tests/shortener/test_units.py`, `test_config_models.py`, `test_safety.py`, `test_ratelimit.py`, `test_analytics.py` | Pure logic: code generation, URL rules, safety policy (per unsafe class plus legitimate counter-examples), token bucket (burst, refill, isolation, eviction, concurrency), hashing/aggregation and cascade delete of click events |
| Product persistence | `test_persistence.py` | Migrations idempotent, repository round-trip, **atomic click increment under 8 threads** |
| Product service | `test_link_service.py`, `test_bug101_regression.py` | Business rules independent of HTTP; BUG-101 regression (written before the fix) |
| Product API | `test_api.py`, `test_stats_api.py`, `test_api_safety.py`, `test_redirect_ratelimit.py` | HTTP contract: status codes, error bodies, headers (`Retry-After`, `X-Request-ID`), 307 redirects |
| Orchestrator unit | `tests/orchestrator/test_graph.py`, `test_policy.py`, `test_components.py` | Graph validation/waves/critical path/mutation; policy rules with blocking and passing cases; audit tamper and deletion detection; lineage; metrics maths (MTTR); provider fallback; Anthropic provider against a mocked HTTP transport |
| Orchestrator engine | `tests/orchestrator/test_engine.py` | With fake agents on real git workspaces: parallelism for disjoint scopes and serialisation for overlapping ones; retry + scoped rollback; exhausted retries block dependents; fallback agent; policy violation → safe-stop with clean workspace; rejection → safe-stop; change request → re-plan with cache reuse; STOP file → safe-stop → resume; entry gate fails without retrying |
| End-to-end | `tests/orchestrator/test_scenarios.py` | The real pipeline for all three scenarios (chained), each fault, resume across sessions, audit chain verification, and **reproducibility**: the chained release and the materialised baseline both equal the committed `shortener/` and `tests/shortener/` byte-for-byte |

## Validation inside the pipeline (runtime gates)

Tests are not only run by developers; the orchestrator runs them as gates:

* **Per task**: `python_compiles` and `targeted_tests_pass`, the tests the plan named as proof for that task.
  Compliance requires every product-code task to name them.
* **Per run**: `all_tests_pass` (full suite, minimum count per scenario so a silently skipped suite cannot pass),
  `api_contract_satisfied` (the implemented OpenAPI must contain every endpoint the approved design declared),
  `no_high_findings` (security scan), `docs_cover_api`, `release_ready` (checklist), `smoke_test_pass`
  (against the *promoted* tree, not the workspace).

## Deliberately not covered

* Load and latency testing of the p99 target: the design argues it (single indexed PK lookup plus one atomic
  update), but no benchmark is included.
* Live Anthropic provider calls (requires a key; covered only with a mocked transport).
* Multi-process concurrency against one SQLite file (single-process threads are covered).

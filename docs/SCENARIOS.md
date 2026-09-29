# Scenarios

Three scenarios run in sequence (`python -m orchestrator demo`); each starts from the previous release. Every
number below comes from the committed [sample runs](sample-runs/). Open a run's `report.md` for the full
timeline, approvals, decision lineage and metrics.

| | Greenfield | Brownfield | Ambiguous |
|---|---|---|---|
| Input | Product brief for v1 | Change request + bug report on v1 | One-paragraph email: "make it safe for public use" |
| Baseline | Empty workspace | Greenfield release (v1.0.0) | Brownfield release (v1.1.0) |
| Implementation tasks | 6 (4 parallel waves) | 5 (test-first bug fix) | 5, then 6 after re-plan |
| Human checkpoints | 6 | 7 | 9 (incl. change request) |
| Recovery exercised | – | Retry + scoped rollback (MTTR 0.8 s) | Governed re-plan, 5 cache hits |
| Tests at release | 44 | 53 | 88 |
| Released version | 1.0.0 | 1.1.0 | 1.2.0 |

---

## 1. Greenfield: build the service from a product brief

**Requirement** ([requirement.md](../scenarios/greenfield/requirement.md)): create/redirect/lookup/delete short
links, vanity codes, expiry, click counter, health endpoints, web URLs only, non-guessable codes, p99 < 50 ms
at 100 rps, survives restart, no external DB.

### Understanding
The brief is mostly well-defined; the vague-term detector finds nothing unquantified ("under 50 ms p99" is
measurable). Two behavioural choices belong to the requester, not the agent, so they are raised at the
`clarification` checkpoint:
* redirect permanence (answer: **307**, because permanent redirects are cached by browsers, which breaks
  click counting and destination changes; recorded as ADR-003)
* delete semantics (answer: hard delete)

### Design (parallel with threat model), human sign-off
Layered architecture (HTTP → service → repository → SQLite with versioned migrations) and ADR-001..003. The
threat model flags 4 residual threats (creation flooding, internal destinations, credential URLs, redirect
flooding). They are recorded, not silently fixed, and carried to release readiness. The ambiguous scenario
later addresses them.

### Decomposition

| Task | Depends on | Scope | Risk | Proof |
|---|---|---|---|---|
| G1 domain model, errors, config | – | `shortener/{__init__,__main__,config,errors,models}.py` | low | `test_config_models.py` |
| G6 dependencies | – | `requirements.txt` | low | dependency allowlist |
| G2 codegen + URL validation | G1 | `codegen.py`, `validation.py` | low | `test_units.py` |
| G3 persistence + schema v1 | G1 | `db.py`, `repository.py`, `conftest.py` | medium | `test_persistence.py` |
| G4 link service | G2, G3 | `link_service.py` | low | `test_link_service.py` |
| G5 HTTP API | G4 | `app.py`, `schemas.py` | medium | `test_api.py` |

Waves: `[G1, G6] → [G2, G3] → [G4] → [G5]`; critical path G1→G3→G4→G5. Each task is proven by its own tests
the moment it lands (bottom-up along the layer dependencies).

### Orchestration highlights
* G6 triggers a **policy-driven** checkpoint (`new_dependency` detected in the diff); G3 triggers one for
  `schema_migration`; G5 for risk above the autonomy limit. None of these were hard-coded per task.
* During development a missing recorded decision for G5 caused a **default-reject safe-stop**: the
  governance rule "silence is not consent" working as designed.
* `verify ∥ security ∥ docs` run in parallel and synchronise at `release_readiness`.

### Validation
44 tests, API contract (6 designed endpoints = 6 implemented), 0 security findings, generated API reference,
readiness checklist, human release approval, smoke test of the promoted tree.

---

## 2. Brownfield: analytics + BUG-101 on the running service

**Requirement** ([requirement.md](../scenarios/brownfield/requirement.md)): CR-17, per-link analytics
(clicks/day, top referrers, unique visitors) with **no raw IPs**; BUG-101, expired links still count
clicks; existing API must not change; database upgraded in place.

### Codebase reasoning
The `codebase` agent parses the real workspace with `ast` ([analysis](sample-runs/2-brownfield/artifacts/codebase-analysis.md)):
6 routes, the `links` table, the import graph. It marks `link_service` and `db` (named in the spec),
`app` (defines `redirect`) and `errors` (defines `LinkExpiredError`) as impacted, computes the blast radius
(`repository`, `validation`, `__main__`) and lists the tests to re-run. It flags two risks: persistence
impacted (forward-only migration needed) and HTTP layer in the blast radius (contract must stay
backward-compatible).

### Understanding
"Unique visitors" and "no raw IPs" conflict. The agent raises it; the human picks a **salted hash** of
IP + user agent (ADR-005). Retention is explicitly deferred (residual risk).

### Decomposition (test-first)

| Task | Depends on | Notes |
|---|---|---|
| B1 schema v2 migration | – | risk high, human review (schema_migration) |
| B2 BUG-101 regression test | B1 | written *first*; expected to fail, so it is not this task's gate |
| B3 analytics module | B1 | parallel with B2 |
| B4 fix BUG-101 + record clicks | B2, B3 | gate: B2's regression test must now pass |
| B5 stats endpoint, v1.1.0 | B4 | public surface changes last |

### Orchestration highlights: recovery
B4's first attempt is a plausible but **incomplete fix**: analytics recording moved after the expiry check,
but the counter increment did not. The targeted regression test fails, the engine rolls back
`link_service.py` only (B2/B3 work untouched), feeds the pytest failure back as feedback, and the second
attempt passes. MTTR 0.8 s; retry frequency and rollback count appear in the metrics.

### Validation
53 tests (all v1 tests unchanged + new), contract now includes `/api/v1/links/{code}/stats`, compliance rule
confirms no PII columns in the migration, readiness shows version bump 1.0.0 → 1.1.0.

---

## 3. Ambiguous: "make it safe for public use"

**Requirement** ([requirement.md](../scenarios/ambiguous/requirement.md)): a four-line email with no
measurable criteria.

### Understanding
* The agent's own detector finds **safe**, **robust** and **public use** without measurable criteria.
* The provider's clarifications cover "safe"/"public use" (threat scope, rate limit, limiter state). The agent
  adds a question the provider missed (*what does "robust" mean here?*).
* The human answers: destinations + creation flooding in scope; 30/min per client; in-memory limiter
  acceptable for a single-node launch; controls fail closed and never affect existing links.
* Answers carry **effects**: they set spec flags (`url_safety`, `create_rate_limit`) and add functional
  requirements and acceptance criteria, each recorded as a human decision.

### Decomposition

| Task | Depends on | Notes |
|---|---|---|
| A1 settings + error types | – | parallel with A3 |
| A3 token-bucket limiter | – | no dependencies |
| A2 destination safety policy | A1 | human review (risk medium) |
| A4 enforce safety in service layer | A2 | every caller gets it, not only HTTP |
| A5 wire into API, v1.2.0 | A3, A4 | |
| A6 redirect rate limit | A5 | *only exists after the re-plan* |

### Orchestration highlights: governed re-plan
1. The first pass completes; the threat model still lists **T5 (redirect flooding)** as residual.
2. At the release checkpoint the reviewer returns **request_changes**: rate-limit redirects too.
3. The engine publishes the amendment, marks `requirements` and all descendants `STALE`, and re-plans:
   * `requirements` re-runs **without re-asking** earlier clarifications, adding flag `redirect_rate_limit`.
   * `design` changes (ADR-009 appears) and goes back through **design sign-off** (occurrence 2).
   * `threat_model` now shows T5 addressed; `plan` emits A6 → **graph mutation adds one node**.
   * A1-A5 fingerprints are unchanged → **5 cache hits**, no rewind, no re-review.
   * A6 runs with its own change review; `verify/security/docs` re-run on the new graph; the changelog
     section is rewritten to include A6.
4. The second release checkpoint approves; the promoted tree passes the smoke test.

### Validation
88 tests (incl. SSRF/metadata-IP, credential-URL, blocklist label-boundary, limiter concurrency and memory
bound, "throttled redirects are not clicks"), 0 security findings, 0 residual threats, lineage from the
release decision back to the original email and the amendment.

---

## Guardrail demonstrations (fault injection)

| Run | Fault | What happens |
|---|---|---|
| [fault-provider-outage](sample-runs/fault-provider-outage/report.md) | Primary provider fails for design and plan | `FallbackProvider` switches to the replay provider; two `fallback.used` events; run succeeds |
| [fault-security-violation-then-resume](sample-runs/fault-security-violation-then-resume/report.md) | Implementation agent leaks `ADMIN_API_TOKEN = "sk_live_…"` | Policy blocks it (`security.secret.live_api_key`), task scope rolled back, **safe-stop** with the workspace at the last good checkpoint; `resume` completes the run on the same audit chain |
| [fault-smoke-failure-rollback](sample-runs/fault-smoke-failure-rollback/report.md) | Promoted release fails its smoke test | Promotion reverted, release node fails, run `FAILED`, no broken release left behind |

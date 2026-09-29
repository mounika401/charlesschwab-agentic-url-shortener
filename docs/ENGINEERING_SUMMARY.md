# Engineering summary

## 1. Plan and rationale

The assignment names orchestration as the critical differentiator, so the effort went into making the
**control layer** production-shaped: explicit, inspectable, testable, and hard to misuse. The product
(URL shortener) is kept deliberately conventional so it can serve as a credible target that exercises every
control path: new code, schema migrations, dependency changes, a public API, a real bug, and security-sensitive
changes.

Build order:

1. **Service first, as three versions** (v1 greenfield, v1.1 analytics + BUG-101, v1.2 hardening), each fully
   tested. These define what "correct output" looks like for each scenario.
2. **Orchestrator core**: data model → graph → context/lineage → audit → policy → workspace → approvals →
   providers → agents/gates → engine → lifecycle/CLI → report.
3. **Scenarios**: each task's change set is a real file set or `git diff` derived from the service versions,
   so implementation steps apply real patches that must pass real tests.
4. **Close the loop**: run scenarios, fix what the runs exposed (below), add engine and end-to-end tests,
   then assert that the chained pipeline reproduces the committed service byte-for-byte.

### What running it exposed (and was fixed)

Treating the prototype's own runs as evidence surfaced defects a design review would likely have missed:

| Finding | Fix |
|---|---|
| Secret rule `\btoken` missed `ADMIN_API_TOKEN` (underscore is a word character), so an injected live key passed the guardrail | Rule now matches credential words inside identifiers; added a `*_live_*` key rule; regression tests for both |
| A spec amendment re-ran **every** implementation task, forcing a workspace rewind and repeated human reviews | Tasks fingerprint their task definition, not the whole spec (the plan is the contract) → incremental re-plan |
| Impact analysis matched keywords as substrings (`rate` in `generate_code`) and in comments, inflating the blast radius | Match whole identifier tokens of definitions and route handlers |
| Threat model ignored controls already present in brownfield code | Code evidence gives status `mitigated-in-code`, distinct from `addressed-in-spec` and `residual-risk` |
| Changelog kept a stale section after re-plan | Version section is replaced, not skipped |
| Wildcard inputs (`change:*`) failed the entry gate literally | Wildcards must match at least one context key |

## 2. Artifacts

| Artifact | Location |
|---|---|
| Orchestrator (engine, graph, policy, approvals, audit, metrics, workspace, providers, 12 agents) | `orchestrator/` |
| Governance policy | `governance/policy.yaml` |
| Product service v1.2.0 and tests (pipeline output) | `shortener/`, `tests/shortener/` |
| API reference, 9 ADRs, 3 design docs, 3 threat models, changelog (agent-generated) | `docs/product/` |
| Run reports, audit trails, metrics, plans, codebase analysis, security scans, release notes | `docs/sample-runs/` |
| Architecture, scenarios, testing docs | `docs/*.md` |

## 3. Risks, trade-offs and validation

### Orchestrator

| Risk / failure scenario | Control | Validated by |
|---|---|---|
| Agent writes outside its remit | Per-node write scopes enforced at write time and on the diff; protected paths; path traversal rejected | `test_policy.py`, `test_engine.py` |
| Agent introduces secrets or dangerous code | Policy scan of every change set; high severity = violation → safe-stop (never retried) | security-violation run and tests |
| Agent produces plausible but wrong code | Per-task targeted tests + full suite + API contract + smoke test | brownfield B4 retry |
| Transient agent/provider failure | Bounded retries with feedback, scoped rollback, fallback agent/provider | engine tests, provider-outage run |
| Irreversible or high-impact change without oversight | Policy-classified actions + risk threshold + mandatory checkpoints; default reject | approvals in every run |
| Parallel writers corrupt each other | Scope locks in the scheduler; planner serialises overlapping tasks | engine parallelism test |
| Requirements change mid-flight | Amendment → STALE propagation → fingerprint cache → rewind only where needed | ambiguous run, engine re-plan test |
| Crash or operator interrupt | Atomic `state.json` after every transition; STOP file / SIGINT → safe-stop; `resume` | stop/resume test |
| Tampered or incomplete audit trail | SHA-256 hash chain with sequence numbers; `verify-audit` | tamper and deletion tests |
| Bad release | Smoke test on the promoted tree; promotion reverted on failure | smoke-failure run |

**Trade-offs taken**

* *Deterministic replay by default vs. live LLM.* Reproducibility and testability of the orchestration were
  prioritised over showing live generation. The governance path is identical for both, and a Claude provider
  is included, but I did not validate it against the live API here.
* *Threads + git in one process vs. distributed workers.* Simple, inspectable and adequate for a prototype.
  The engine's worker interface (pure function of spec + snapshot → result) is what would move to a queue.
* *Rule-based threat model and vague-term detector.* Transparent and testable, but keyword-driven. They
  complement human review; they don't replace it.
* *Scoped rollback via git* is precise for files. It does not undo external side effects; the release
  step handles its own compensation explicitly.

### Product

| Risk | Decision | Residual |
|---|---|---|
| Lost click updates under concurrency | Atomic SQL increment | – |
| Guessable/enumerable codes | CSPRNG base62, 62^7 space, bounded collision retry | – |
| PII in analytics | Salted truncated hash; referrer host only; no PII columns (policy-checked) | Unbounded event retention (deferred with owner) |
| Phishing / internal targets | Syntactic safety policy (credentials, internal hosts, non-global IPs, own domain, blocklist) | DNS names resolving to private IPs (documented, ADR-007) |
| Abuse by scripting | Token-bucket limits on creation and redirects, `Retry-After` | Per-instance state: N replicas = N × limit (accepted at clarification) |
| Client identity behind proxies | Key on peer address; do not trust `X-Forwarded-For` | Needs trusted-proxy configuration when deployed behind a load balancer |

## 4. Assumptions

* Single-node deployment for the prototype (confirmed at clarification for rate-limit state).
* The management API is internal and unauthenticated in v1 (explicitly out of scope; flagged as next step).
* Recorded approval files represent decisions a named human made; interactive mode is the real
  human-in-the-loop path and uses the same code.
* Python 3.10+, git on PATH; the product's runtime dependencies are those in `requirements.txt`.

## 5. Limitations and next steps

1. **Provider realism**: the scripted provider replays reviewed responses (including a deliberately flawed
   attempt). Next: run the Anthropic provider under the same gates, record its outputs, and compare
   success rate, retries and MTTR against the replay baseline.
2. **Execution isolation**: agent-generated tests run in-process on the host. Production use needs a sandbox
   (container, no network, resource limits) for the verify and smoke steps.
3. **Durability and scale**: state is a JSON file and workers are threads. Next: a persistent store (e.g.
   Postgres) for run state and a work queue for agents; the engine's pure worker contract makes this a
   contained change.
4. **Approval UX**: terminal prompts or files. Next: approvals as PR reviews or chat actions with SSO
   identity, keeping the same `Approver` interface.
5. **Policy expressiveness**: YAML rules + regexes. Next: OPA/Rego policies and a real SAST/dependency scanner
   (bandit, pip-audit) behind the same `security` node.
6. **Product next steps**: authentication for the management API, a retention job for click events, a shared
   (Redis) rate limiter before scaling out, a benchmark for the p99 target.

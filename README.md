# Agentic URL Shortener — governed, agentic SDLC orchestration

A working prototype that turns a requirement into a reviewable, released engineering outcome through an
**agentic orchestration layer**: agents do the multi-step work (requirements, design, threat modelling,
planning, implementation, verification, security, documentation, release); a **policy engine** decides what
they may do alone; **humans** own clarifications, approvals and final quality.

The delivered product is a URL shortener service (FastAPI + SQLite, v1.2.0). It was not written separately:
the committed `shortener/` package and its tests are the output of the pipeline running three scenarios in
sequence, and a test asserts the pipeline reproduces them byte-for-byte.

```
requirement ─► requirements ─┬─► design ──(human sign-off)──┐
   (text)       (clarify w/   ├─► threat model ─────────────┼─► plan ─► impl tasks (dynamic DAG, parallel,
                 human)       └─► codebase analysis ────────┘           scope-locked, test-gated, retried)
                                                                              │
             release ◄─ (human release approval) ◄─ readiness ◄─┬─ verify (full suite + API contract)
      (smoke-tested,                                            ├─ security scan (policy rules)
       rolled back on failure)                                  └─ docs (from live OpenAPI)
```

## Quick start

Requirements: Python 3.10+ and git.

```bash
git clone <this repo> && cd agentic-url-shortener
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m pytest                          # 150+ tests: service + orchestrator + end-to-end scenarios (~40 s)
python -m orchestrator demo               # greenfield -> brownfield -> ambiguous, each building on the last release
```

Each run writes `runs/<run-id>/` containing `report.md` (start here), `audit.jsonl`, `metrics.json`,
`state.json`, `artifacts/`, the git `workspace/` (one commit per checkpoint) and the promoted `release/`.

### Run the service itself

```bash
python -m shortener                        # http://127.0.0.1:8000/docs
curl -s -X POST localhost:8000/api/v1/links -H 'content-type: application/json' \
     -d '{"url": "https://example.com/very/long/path", "ttl_seconds": 3600}'
curl -si localhost:8000/<code>             # 307 -> destination
curl -s localhost:8000/api/v1/links/<code>/stats
```

Configuration is environment-driven (`SHORTENER_DB_PATH`, `SHORTENER_BASE_URL`, `SHORTENER_ANALYTICS_SALT`,
`SHORTENER_CREATE_RATE_LIMIT`, `SHORTENER_REDIRECT_RATE_LIMIT`, `SHORTENER_BLOCKED_DOMAINS`, …); see
[`shortener/config.py`](shortener/config.py) and the generated [API reference](docs/product/API.md).

## Orchestrator commands

```bash
python -m orchestrator run greenfield|brownfield|ambiguous   # one scenario (recorded human decisions)
python -m orchestrator run ambiguous --interactive           # YOU are the approver at every checkpoint
python -m orchestrator run greenfield --fault provider_outage      # primary provider down -> fallback
python -m orchestrator run greenfield --fault security_violation   # agent leaks a secret -> safe-stop
python -m orchestrator run greenfield --fault smoke_failure        # bad release -> promotion rolled back
python -m orchestrator stop <run-id>        # request safe-stop of a running run
python -m orchestrator resume <run-id>      # continue a stopped/failed run from persisted state
python -m orchestrator status <run-id>      # node-by-node state
python -m orchestrator lineage <run-id> release:readiness   # why was this decided? trace inputs
python -m orchestrator verify-audit <run-id>                # check the audit hash chain
python -m orchestrator metrics              # success rate, retry/rollback frequency, MTTR, latency
python -m orchestrator run greenfield --provider anthropic  # Claude as primary provider (needs ANTHROPIC_API_KEY)
```

## What to look at

| If you want to see… | Look at |
|---|---|
| The architecture, control flow and design decisions | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| The three scenarios: decomposition, orchestration, validation | [docs/SCENARIOS.md](docs/SCENARIOS.md) |
| A full run, end to end, with timeline, approvals, lineage and metrics | [docs/sample-runs/3-ambiguous/report.md](docs/sample-runs/3-ambiguous/report.md) |
| Guardrails firing (fallback, policy violation + resume, rollback) | [docs/sample-runs/](docs/sample-runs/) `fault-*` |
| Testing approach | [docs/TESTING.md](docs/TESTING.md) |
| Plan, rationale, risks, trade-offs, assumptions, limitations | [docs/ENGINEERING_SUMMARY.md](docs/ENGINEERING_SUMMARY.md) |
| Governance rules agents run under | [governance/policy.yaml](governance/policy.yaml) |
| Agent-produced product docs (ADRs, designs, threat models, API) | [docs/product/](docs/product/) |

## Repository layout

```
orchestrator/            the agentic orchestration layer
  engine.py              scheduler: gates, parallelism, retries, rollback, approvals, re-plan, safe-stop
  graph.py               dependency graph (validation, waves, critical path, runtime mutation)
  policy.py              guardrails: change control, security, compliance, approval routing
  approvals.py           human checkpoints (interactive or recorded decisions)
  context.py             cross-stage context store with provenance + decision lineage
  audit.py / metrics.py  hash-chained audit trail; reliability metrics derived from it
  workspace.py           git-backed workspace: scoped writes, checkpoints, scoped/full rollback
  providers.py           content generation (scripted replay, Anthropic) with fallback chain
  lifecycle.py           SDLC graph template, scenario loading, run construction/resume
  agents/                requirements, codebase, design, threat_model, plan, implement,
                         verify, security, docs, release_readiness, checkpoint, release
governance/policy.yaml   autonomy limits, mandatory checkpoints, protected paths, security/compliance rules
scenarios/<name>/        requirement, recorded approvals, provider responses (spec/design/plan/change sets)
shortener/               the product (output of the pipeline)
tests/shortener/         product tests (written by the pipeline)
tests/orchestrator/      orchestrator unit, engine and end-to-end tests
docs/                    architecture, scenarios, testing, summary, sample runs, generated product docs
```

## Author

Venkata Sai Avinash Kanna Pusuluri. Built with AI assistance (Claude), as the assignment permits.

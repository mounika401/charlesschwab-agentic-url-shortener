"""Command-line interface.

    python -m orchestrator run <scenario> [--interactive] [--fault NAME] [--provider scripted|anthropic]
    python -m orchestrator demo                 # greenfield -> brownfield -> ambiguous, chained releases
    python -m orchestrator resume <run_id> [--interactive]
    python -m orchestrator stop <run_id>        # request safe-stop of a running run
    python -m orchestrator status <run_id>
    python -m orchestrator verify-audit <run_id>
    python -m orchestrator lineage <run_id> <context-key-or-decision-id>
    python -m orchestrator metrics
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import lifecycle
from .audit import verify_chain
from .context import ContextStore
from .metrics import aggregate

FAULTS = ["provider_outage", "security_violation", "smoke_failure"]


def _print_result(engine) -> None:
    metrics = json.loads((engine.run_dir / "metrics.json").read_text())
    print(f"\nrun {engine.run_id}: {engine.status.value.upper()}")
    print(f"  report   : {engine.run_dir / 'report.md'}")
    print(f"  audit    : {engine.run_dir / 'audit.jsonl'}")
    print(f"  release  : {engine.release_dir}")
    keys = ["end_to_end_latency_s", "node_success_rate", "retries", "rollbacks", "fallbacks", "replans",
            "approvals", "policy_violations", "mttr_s"]
    print("  metrics  : " + ", ".join(f"{k}={metrics[k]}" for k in keys))


def cmd_run(args) -> int:
    engine = lifecycle.create_run(
        args.scenario, interactive=args.interactive, approvals_file=args.approvals,
        provider_mode=args.provider, faults=set(args.fault or []),
        baseline_from=Path(args.baseline_from) if args.baseline_from else None,
    )
    print(f"run {engine.run_id} started ({engine.scenario['title']})")
    status = engine.run()
    _print_result(engine)
    return 0 if status.value == "succeeded" else 1


def cmd_demo(args) -> int:
    previous_release = None
    code = 0
    for name in ("greenfield", "brownfield", "ambiguous"):
        engine = lifecycle.create_run(name, baseline_from=previous_release, provider_mode=args.provider)
        print(f"\n=== {name}: {engine.scenario['title']} ===")
        status = engine.run()
        _print_result(engine)
        if status.value != "succeeded":
            return 1
        previous_release = engine.release_dir
    print("\n" + json.dumps(aggregate(lifecycle.RUNS_DIR / "metrics_history.jsonl"), indent=2))
    return code


def cmd_resume(args) -> int:
    engine = lifecycle.resume_run(args.run_id, interactive=args.interactive, approvals_file=args.approvals)
    status = engine.run(resumed=True)
    _print_result(engine)
    return 0 if status.value == "succeeded" else 1


def cmd_stop(args) -> int:
    run_dir = lifecycle.RUNS_DIR / args.run_id
    (run_dir / "STOP").write_text("safe-stop requested\n")
    print(f"safe-stop requested for {args.run_id}; it halts at the next scheduling point")
    return 0


def _state(run_id: str) -> dict:
    return json.loads((lifecycle.RUNS_DIR / run_id / "state.json").read_text())


def cmd_status(args) -> int:
    state = _state(args.run_id)
    print(f"{args.run_id}: {state['status']}  (graph v{state['graph_version']})")
    for node in state["nodes"]:
        st = state["states"][node["id"]]
        print(f"  {node['id']:<28} {st['status']:<12} attempts={st['attempts']} runs={st['runs']}"
              + (f"  error={st['error'][:80]}" if st.get("error") else ""))
    return 0


def cmd_verify_audit(args) -> int:
    ok, message = verify_chain(lifecycle.RUNS_DIR / args.run_id / "audit.jsonl")
    print(("OK: " if ok else "TAMPERED: ") + message)
    return 0 if ok else 2


def cmd_lineage(args) -> int:
    store = ContextStore.from_dict(_state(args.run_id)["context"])
    print("\n".join(store.lineage(args.ref)))
    return 0


def cmd_metrics(args) -> int:
    print(json.dumps(aggregate(lifecycle.RUNS_DIR / "metrics_history.jsonl"), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orchestrator", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one scenario end to end")
    run.add_argument("scenario", choices=["greenfield", "brownfield", "ambiguous"])
    run.add_argument("--interactive", action="store_true", help="prompt a human at every checkpoint")
    run.add_argument("--approvals", help="recorded approvals file (default: the scenario's approvals.yaml)")
    run.add_argument("--provider", choices=["scripted", "anthropic"], default="scripted")
    run.add_argument("--fault", action="append", choices=FAULTS, help="inject a fault to exercise guardrails")
    run.add_argument("--baseline-from", help="start from a previous run's release directory")
    run.set_defaults(func=cmd_run)

    demo = sub.add_parser("demo", help="run all three scenarios, each building on the previous release")
    demo.add_argument("--provider", choices=["scripted", "anthropic"], default="scripted")
    demo.set_defaults(func=cmd_demo)

    resume = sub.add_parser("resume", help="resume a stopped or failed run")
    resume.add_argument("run_id")
    resume.add_argument("--interactive", action="store_true")
    resume.add_argument("--approvals")
    resume.set_defaults(func=cmd_resume)

    for name, func, helptext in (("stop", cmd_stop, "request safe-stop"), ("status", cmd_status, "show node states"),
                                 ("verify-audit", cmd_verify_audit, "verify the audit hash chain")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("run_id")
        p.set_defaults(func=func)

    lineage = sub.add_parser("lineage", help="trace how a context value or decision was derived")
    lineage.add_argument("run_id")
    lineage.add_argument("ref")
    lineage.set_defaults(func=cmd_lineage)

    sub.add_parser("metrics", help="aggregate reliability metrics across runs").set_defaults(func=cmd_metrics)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

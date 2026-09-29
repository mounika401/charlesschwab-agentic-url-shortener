"""Entry and exit gates.

Entry gates decide whether a node may *start* (its inputs are present and the
workspace scope it owns is clean). Exit gates decide whether a node's output is
acceptable. A failing exit gate is fed back to the agent as retry feedback;
a failing entry gate fails the node immediately because retrying cannot fix
missing inputs.
"""

from __future__ import annotations

import fnmatch
import py_compile
import subprocess
import sys
from typing import Callable

from .agents.base import AgentContext
from .models import AgentResult, GateFailure

ExitGate = Callable[[AgentContext, AgentResult], None]
EXIT_GATES: dict[str, ExitGate] = {}


def exit_gate(name: str):
    def register(fn: ExitGate) -> ExitGate:
        EXIT_GATES[name] = fn
        return fn

    return register


def check_entry(ctx: AgentContext, gates: list[str], context_keys: set[str]) -> None:
    for gate in gates:
        if gate == "inputs_present":
            missing = []
            for key in ctx.node.params.get("inputs", []):
                if any(ch in key for ch in "*?["):
                    if not fnmatch.filter(context_keys, key):
                        missing.append(key)  # a wildcard input must match at least one value
                elif key not in context_keys:
                    missing.append(key)
            if missing:
                raise GateFailure(gate, f"missing inputs {missing}")
        elif gate == "scope_clean":
            dirty = ctx.workspace.pending_changes(ctx.node.write_scope) if ctx.node.write_scope else []
            if dirty:
                raise GateFailure(gate, f"uncommitted changes in scope: {[c.path for c in dirty]}")
        else:
            raise GateFailure(gate, "unknown entry gate")


def run_exit_gates(ctx: AgentContext, result: AgentResult, gates: list[str]) -> list[str]:
    passed = []
    for name in gates:
        if name not in EXIT_GATES:
            raise GateFailure(name, "unknown exit gate")
        EXIT_GATES[name](ctx, result)
        passed.append(name)
    return passed


def run_pytest(ctx: AgentContext, targets: list[str], timeout: int = 300) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", *targets]
    try:
        proc = subprocess.run(cmd, cwd=ctx.workspace.root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {timeout}s"
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
    return proc.returncode == 0, tail


# ---- requirements -------------------------------------------------------------------
@exit_gate("spec_complete")
def spec_complete(ctx: AgentContext, result: AgentResult) -> None:
    spec = result.outputs.get("spec", {})
    if not spec.get("goal"):
        raise GateFailure("spec_complete", "spec has no goal")
    if not spec.get("acceptance_criteria"):
        raise GateFailure("spec_complete", "spec has no acceptance criteria")
    if spec.get("open_questions"):
        raise GateFailure("spec_complete", f"unresolved questions: {spec['open_questions']}")


# ---- design -------------------------------------------------------------------------
@exit_gate("design_complete")
def design_complete(ctx: AgentContext, result: AgentResult) -> None:
    design = result.outputs.get("design", {})
    if not design.get("endpoints"):
        raise GateFailure("design_complete", "design declares no API endpoints")
    for adr in design.get("adrs", []):
        if not adr.get("rationale") or not adr.get("alternatives"):
            raise GateFailure("design_complete", f"{adr.get('id')} lacks rationale or alternatives")


# ---- planning -----------------------------------------------------------------------
@exit_gate("plan_valid")
def plan_valid(ctx: AgentContext, result: AgentResult) -> None:
    plan = result.outputs.get("plan", {})
    if not plan.get("tasks"):
        raise GateFailure("plan_valid", "plan has no tasks")
    problems = plan.get("problems", [])
    if problems:
        raise GateFailure("plan_valid", "; ".join(problems))


# ---- implementation -----------------------------------------------------------------
def _change(ctx: AgentContext, result: AgentResult) -> dict:
    return result.outputs.setdefault(f"change:{ctx.node.params.get('task', {}).get('id')}", {})


@exit_gate("python_compiles")
def python_compiles(ctx: AgentContext, result: AgentResult) -> None:
    for rel in _change(ctx, result).get("files", []):
        if rel.endswith(".py") and (ctx.workspace.root / rel).exists():
            try:
                py_compile.compile(str(ctx.workspace.root / rel), doraise=True)
            except py_compile.PyCompileError as exc:
                raise GateFailure("python_compiles", f"{rel}: {exc.msg.strip()}") from exc


@exit_gate("targeted_tests_pass")
def targeted_tests_pass(ctx: AgentContext, result: AgentResult) -> None:
    targets = [t for t in ctx.node.params.get("task", {}).get("targeted_tests", [])
               if (ctx.workspace.root / t).exists()]
    if not targets:
        return
    ok, tail = run_pytest(ctx, targets)
    _change(ctx, result)["targeted_tests"] = {"targets": targets, "passed": ok}
    if not ok:
        raise GateFailure("targeted_tests_pass", f"targeted tests failed:\n{tail}")


# ---- verification -------------------------------------------------------------------
@exit_gate("all_tests_pass")
def all_tests_pass(ctx: AgentContext, result: AgentResult) -> None:
    v = result.outputs.get("verification", {})
    if not v.get("ok") or v.get("failed", 1) != 0:
        raise GateFailure("all_tests_pass", f"{v.get('failed')} failing tests:\n{v.get('tail', '')}")
    minimum = ctx.node.params.get("min_tests", 1)
    if v.get("passed", 0) < minimum:
        raise GateFailure("all_tests_pass", f"only {v.get('passed')} tests ran; expected >= {minimum}")


@exit_gate("api_contract_satisfied")
def api_contract_satisfied(ctx: AgentContext, result: AgentResult) -> None:
    missing = result.outputs.get("verification", {}).get("contract", {}).get("missing", [])
    if missing:
        raise GateFailure("api_contract_satisfied", f"designed endpoints not implemented: {missing}")


@exit_gate("no_high_findings")
def no_high_findings(ctx: AgentContext, result: AgentResult) -> None:
    high = [f for f in result.outputs.get("security", {}).get("findings", []) if f["severity"] == "high"]
    if high:
        raise GateFailure("no_high_findings", f"{len(high)} high-severity findings, first: {high[0]}")


@exit_gate("docs_cover_api")
def docs_cover_api(ctx: AgentContext, result: AgentResult) -> None:
    docs = result.outputs.get("docs", {})
    if docs.get("undocumented"):
        raise GateFailure("docs_cover_api", f"endpoints missing from API docs: {docs['undocumented']}")


@exit_gate("release_ready")
def release_ready(ctx: AgentContext, result: AgentResult) -> None:
    failing = [c for c in result.outputs.get("readiness", {}).get("checks", []) if not c["passed"]]
    if failing:
        raise GateFailure("release_ready", "; ".join(f"{c['name']}: {c['detail']}" for c in failing))


@exit_gate("smoke_test_pass")
def smoke_test_pass(ctx: AgentContext, result: AgentResult) -> None:
    release = result.outputs.get("release", {})
    if not release.get("smoke_passed"):
        raise GateFailure("smoke_test_pass", release.get("smoke_detail", "smoke test did not pass"))

"""SDLC lifecycle template, scenario loading and run construction."""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from .agents.release import read_version
from .approvals import InteractiveApprover, ScriptedApprover
from .context import ContextStore
from .engine import Engine
from .graph import Graph
from .models import NodeSpec, NodeState, NodeStatus, RetryPolicy, Risk
from .policy import Policy
from .providers import (
    AnthropicProvider,
    FallbackProvider,
    FaultInjectingProvider,
    ScriptedProvider,
    active,
)
from .workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"
RUNS_DIR = REPO_ROOT / "runs"
POLICY_PATH = REPO_ROOT / "governance" / "policy.yaml"

SCAFFOLD_PYPROJECT = """[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
"""


def load_scenario(name: str) -> dict[str, Any]:
    path = SCENARIOS_DIR / name / "scenario.yaml"
    if not path.exists():
        raise FileNotFoundError(f"unknown scenario '{name}' (expected {path})")
    data = yaml.safe_load(path.read_text())
    data["dir"] = str(path.parent)
    return data


def lifecycle_graph(scenario: dict[str, Any]) -> Graph:
    """The static SDLC skeleton. Implementation tasks are added at runtime by the planner."""
    brownfield = bool(scenario.get("baseline"))
    design_deps = ["requirements"] + (["codebase"] if brownfield else [])
    nodes = [
        NodeSpec("requirements", "requirements", "requirements",
                 params={"inputs": ["requirement", "amendments", "clarification_answers"]},
                 exit_gates=["spec_complete"]),
        NodeSpec("design", "design", "design", depends_on=design_deps,
                 params={"inputs": ["spec"] + (["codebase"] if brownfield else [])},
                 exit_gates=["design_complete"], write_scope=["docs/design/**", "docs/adr/**"],
                 risk=Risk.MEDIUM, approval="design_signoff"),
        NodeSpec("threat_model", "design", "threat_model", depends_on=["requirements"],
                 params={"inputs": ["spec"]}, write_scope=["docs/security/**"]),
        NodeSpec("plan", "plan", "plan", depends_on=design_deps[1:] + ["design", "threat_model"],
                 params={"inputs": ["spec", "design", "threats"] + (["codebase"] if brownfield else [])},
                 exit_gates=["plan_valid"]),
        NodeSpec("verify", "verify", "verify", depends_on=["plan"],
                 params={"inputs": ["design"], "min_tests": scenario.get("min_tests", 1)},
                 exit_gates=["all_tests_pass", "api_contract_satisfied"], retry=RetryPolicy(max_attempts=1)),
        NodeSpec("security", "verify", "security", depends_on=["plan"], params={"inputs": []},
                 exit_gates=["no_high_findings"], retry=RetryPolicy(max_attempts=1)),
        NodeSpec("docs", "docs", "docs", depends_on=["plan"], params={"inputs": ["spec", "plan", "design"]},
                 exit_gates=["docs_cover_api"], write_scope=["docs/API.md", "CHANGELOG.md"]),
        NodeSpec("release_readiness", "release", "release_readiness", depends_on=["verify", "security", "docs"],
                 params={"inputs": ["spec", "plan", "verification", "security", "docs", "threats", "baseline",
                                    "approvals", "change:*"]},
                 exit_gates=["release_ready"], retry=RetryPolicy(max_attempts=1)),
        NodeSpec("release_approval", "release", "checkpoint", depends_on=["release_readiness"],
                 params={"inputs": ["readiness"]}, approval="release", risk=Risk.HIGH),
        NodeSpec("release", "release", "release", depends_on=["release_approval"],
                 params={"inputs": []}, exit_gates=["smoke_test_pass"], retry=RetryPolicy(max_attempts=1)),
    ]
    if brownfield:
        nodes.insert(1, NodeSpec("codebase", "requirements", "codebase", depends_on=["requirements"],
                                 params={"inputs": ["spec"], "package": "shortener"}))
    return Graph(nodes)


# ---- baseline construction ------------------------------------------------------------
def scaffold(workspace: Workspace) -> str:
    workspace.init()
    (workspace.root / "pyproject.toml").write_text(SCAFFOLD_PYPROJECT)
    return workspace.checkpoint("baseline: project scaffold (pytest config)")


def materialize(workspace: Workspace, chain: list[str]) -> str:
    """Rebuild the end state of earlier scenarios by applying their final change sets.

    Used for standalone runs so brownfield scenarios start from a known,
    reproducible codebase without re-running the earlier pipelines.
    """
    head = workspace.head()
    for name in chain:
        scenario = load_scenario(name)
        provider = ScriptedProvider(Path(scenario["dir"]) / "provider")
        flags = set(scenario.get("materialize_flags", []))
        plan = provider.generate("plan", {"flags": sorted(flags)})
        graph = Graph([NodeSpec(t["id"], "task", "-", depends_on=t.get("depends_on", [])) for t in plan["tasks"]])
        for tid in graph.topological_order():
            task = next(t for t in plan["tasks"] if t["id"] == tid)
            change = provider.generate("implement", {"task": task, "attempt": 0})
            scope = ["**"]
            if "patch" in change:
                workspace.apply_patch(change["patch"], scope)
            else:
                for path, content in change["files"].items():
                    workspace.write_file(path, content, scope)
        head = workspace.checkpoint(f"baseline: {name} release")
    return head


def copy_baseline(workspace: Workspace, release_dir: Path) -> str:
    for item in release_dir.iterdir():
        if item.name in {".git", ".gitignore"}:
            continue
        dest = workspace.root / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, dest)
    return workspace.checkpoint(f"baseline: copied from {release_dir}")


# ---- run construction -----------------------------------------------------------------
def make_provider(mode: str, scenario: dict[str, Any], faults: set[str], on_fallback) -> FallbackProvider:
    scripted = ScriptedProvider(Path(scenario["dir"]) / "provider")
    chain = []
    if mode == "anthropic":
        chain.append(AnthropicProvider())
    if "provider_outage" in faults:
        chain.append(FaultInjectingProvider(scripted, {"design", "plan"}, name="primary"))
    chain.append(scripted)
    return FallbackProvider(chain, on_fallback=on_fallback)


def make_approver(scenario: dict[str, Any], interactive: bool, approvals_file: str | None):
    if interactive:
        return InteractiveApprover()
    path = Path(approvals_file) if approvals_file else Path(scenario["dir"]) / scenario.get("approvals", "approvals.yaml")
    return ScriptedApprover(path)


def create_run(
    scenario_name: str,
    *,
    interactive: bool = False,
    approvals_file: str | None = None,
    provider_mode: str = "scripted",
    faults: set[str] | None = None,
    baseline_from: Path | None = None,
    runs_dir: Path = RUNS_DIR,
    run_id: str | None = None,
) -> Engine:
    scenario = load_scenario(scenario_name)
    faults = set(faults or ())
    run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{scenario_name}-{uuid.uuid4().hex[:4]}"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    workspace = Workspace(run_dir / "workspace")
    scaffold(workspace)
    if scenario.get("baseline"):
        commit = copy_baseline(workspace, baseline_from) if baseline_from else materialize(workspace, scenario["baseline"])
    else:
        commit = workspace.head()

    context = ContextStore()
    requirement = (Path(scenario["dir"]) / scenario["requirement"]).read_text()
    context.publish("requirement", requirement, produced_by=f"human:{scenario.get('requested_by', 'requester')}", run=1)
    context.publish("baseline", {"commit": commit, "version": read_version(workspace.root),
                                 "source": str(baseline_from) if baseline_from else scenario.get("baseline", [])},
                    produced_by="engine", run=1)
    for key, empty in (("amendments", []), ("clarification_answers", {}), ("approvals", [])):
        context.publish(key, empty, produced_by="engine", run=0)

    engine_ref: dict[str, Engine] = {}

    def on_fallback(kind: str, failed: str, next_provider: str, error: str) -> None:
        engine_ref["e"].audit.record("fallback.used", kind=kind, failed_provider=failed,
                                     next_provider=next_provider, error=error[:500])

    engine = Engine(
        run_id=run_id, run_dir=run_dir, scenario=scenario, graph=lifecycle_graph(scenario),
        workspace=workspace, policy=Policy.load(POLICY_PATH),
        provider=make_provider(provider_mode, scenario, faults, on_fallback),
        approver=make_approver(scenario, interactive, approvals_file), context=context, faults=faults,
    )
    engine_ref["e"] = engine
    engine.save_state()
    return engine


def resume_run(run_id: str, *, interactive: bool = False, approvals_file: str | None = None,
               provider_mode: str = "scripted", runs_dir: Path = RUNS_DIR) -> Engine:
    import json

    run_dir = runs_dir / run_id
    state = json.loads((run_dir / "state.json").read_text())
    scenario = state["scenario"]
    graph = Graph([NodeSpec.from_dict(n) for n in state["nodes"]])
    states = {n: NodeState.from_dict(s) for n, s in state["states"].items()}
    for st in states.values():
        # Anything interrupted or failed gets a fresh chance; succeeded work is kept.
        if st.status in (NodeStatus.RUNNING, NodeStatus.FAILED, NodeStatus.BLOCKED, NodeStatus.AWAITING_APPROVAL):
            st.status = NodeStatus.STALE if st.output_hash else NodeStatus.PENDING
    (run_dir / "STOP").unlink(missing_ok=True)
    engine_ref: dict[str, Engine] = {}

    def on_fallback(kind, failed, next_provider, error):
        engine_ref["e"].audit.record("fallback.used", kind=kind, failed_provider=failed,
                                     next_provider=next_provider, error=error[:500])

    engine = Engine(
        run_id=run_id, run_dir=run_dir, scenario=scenario, graph=graph, workspace=Workspace(run_dir / "workspace"),
        policy=Policy.load(POLICY_PATH), provider=make_provider(provider_mode, scenario, set(), on_fallback),
        approver=make_approver(scenario, interactive, approvals_file),
        context=ContextStore.from_dict(state["context"]), states=states, faults=set(),
    )
    engine.approvals = state.get("approvals", [])
    engine.approval_counts = state.get("approval_counts", {})
    engine.removed = state.get("removed", {})
    engine_ref["e"] = engine
    return engine


def plan_tasks(scenario_name: str, flags: set[str]) -> list[dict[str, Any]]:
    scenario = load_scenario(scenario_name)
    plan = yaml.safe_load((Path(scenario["dir"]) / "provider" / "plan.yaml").read_text())
    return [t for t in plan["tasks"] if active(t, flags)]

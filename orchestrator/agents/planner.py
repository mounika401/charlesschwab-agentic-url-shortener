"""Planner agent: task decomposition into an executable sub-graph.

The provider proposes tasks; the planner owns their validity. It checks the
task graph is acyclic, scopes are legal, code tasks name the tests that prove
them, and that no two tasks which could run concurrently write the same files
(if they would, it serialises them and records why). The validated plan is
returned as a *graph mutation* that the engine applies at runtime.
"""

from __future__ import annotations

from ..graph import Graph, GraphError, in_scope, scopes_overlap
from ..models import AgentResult, Decision, NodeSpec, RetryPolicy, Risk
from .base import AgentContext, AgentError

JOIN_NODES = ["verify", "security", "docs"]


class PlannerAgent:
    name = "plan"

    def run(self, ctx: AgentContext) -> AgentResult:
        spec = ctx.inputs["spec"]
        raw = ctx.provider.generate("plan", {
            "spec": spec, "design": ctx.inputs.get("design"), "codebase": ctx.inputs.get("codebase"),
            "threats": ctx.inputs.get("threats"), "flags": spec.get("flags", []), "feedback": ctx.feedback,
        })
        tasks = raw.get("tasks") or []
        if not tasks:
            raise AgentError("provider returned no tasks")

        problems: list[str] = []
        decisions: list[Decision] = []
        ids = [t["id"] for t in tasks]
        if len(ids) != len(set(ids)):
            problems.append("duplicate task ids")
        for t in tasks:
            t.setdefault("depends_on", [])
            t.setdefault("risk", "low")
            for dep in t["depends_on"]:
                if dep not in ids:
                    problems.append(f"{t['id']} depends on unknown task {dep}")
            if not t.get("write_scope"):
                problems.append(f"{t['id']} has no write_scope")
            for path in t.get("write_scope", []):
                if in_scope(path, ctx.policy.protected_paths):
                    problems.append(f"{t['id']} scope {path} is a protected path")
            touches_code = any(p.startswith("shortener/") for p in t.get("write_scope", []))
            if touches_code and ctx.policy.config["compliance"]["require_targeted_tests"] \
                    and not t.get("targeted_tests"):
                problems.append(f"{t['id']} changes product code but names no targeted tests")

        def to_graph() -> Graph:
            return Graph([NodeSpec(id=t["id"], stage="task", agent="-", depends_on=list(t["depends_on"]))
                          for t in tasks])

        try:
            graph = to_graph()
            # Serialise concurrently-runnable tasks with overlapping write scopes.
            for i, a in enumerate(tasks):
                for b in tasks[i + 1:]:
                    ordered = b["id"] in graph.descendants(a["id"]) or a["id"] in graph.descendants(b["id"])
                    if not ordered and scopes_overlap(a["write_scope"], b["write_scope"]):
                        b["depends_on"].append(a["id"])
                        graph = to_graph()
                        decisions.append(Decision(
                            id=f"plan:serialise:{b['id']}", node=ctx.node.id,
                            summary=f"{b['id']} now runs after {a['id']} (overlapping write scope)",
                            rationale="parallel writers on the same files cannot be rolled back independently",
                            based_on=["plan"]))
            waves = graph.levels()
            weights = {t["id"]: float(t.get("estimate", 1)) for t in tasks}
            critical = graph.critical_path(weights)
        except GraphError as exc:
            problems.append(str(exc))
            waves, critical = [], []

        max_attempts = int(ctx.policy.config["retries"]["default_max_attempts"])
        nodes = [NodeSpec(
            id=f"impl:{t['id']}",
            stage="implement",
            agent="implement",
            depends_on=["plan"] + [f"impl:{d}" for d in t["depends_on"]],
            # The task definition is the contract between spec and code: a spec
            # change only re-runs this task if the planner changes the task.
            params={"task": t, "inputs": ["spec"], "fingerprint_exclude": ["plan", "spec"]},
            entry_gates=["scope_clean"],
            exit_gates=["python_compiles", "targeted_tests_pass"],
            write_scope=list(t["write_scope"]),
            risk=Risk(t["risk"]),
            retry=RetryPolicy(max_attempts=max_attempts),
            rerun_strategy="rewind",
            dynamic=True,
        ).to_dict() for t in tasks]

        lines = [f"# Implementation plan ({ctx.scenario['name']})", "", raw.get("rationale", ""), "",
                 "| Task | Title | Depends on | Scope | Risk | Tests |", "|---|---|---|---|---|---|"]
        lines += [f"| {t['id']} | {t['title']} | {', '.join(t['depends_on']) or '-'} | "
                  f"{', '.join(t['write_scope'])} | {t['risk']} | {', '.join(t.get('targeted_tests', []))} |"
                  for t in tasks]
        lines += ["", f"Parallel waves: {waves}", f"Critical path: {' -> '.join(critical)}"]
        (ctx.artifacts_dir / "plan.md").write_text("\n".join(lines) + "\n")

        decisions.append(Decision(
            id="plan:decomposition", node=ctx.node.id,
            summary=f"{len(tasks)} tasks in {len(waves)} waves; critical path {critical}",
            rationale=raw.get("rationale", ""), based_on=["spec", "design"]))
        return AgentResult(
            outputs={
                "plan": {"tasks": tasks, "waves": waves, "critical_path": critical, "problems": problems},
                "_graph_mutation": {"nodes": nodes, "join": JOIN_NODES},
            },
            decisions=decisions,
            summary=f"{len(tasks)} tasks, {len(waves)} waves",
        )

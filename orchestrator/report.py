"""Human-readable run report generated from engine state + audit trail."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import metrics as metrics_mod

if TYPE_CHECKING:
    from .engine import Engine

INTERESTING = {
    "node.started", "node.succeeded", "node.failed", "node.cached", "attempt.failed", "retry.scheduled",
    "rollback.performed", "fallback.used", "approval.requested", "approval.decided", "replan.triggered",
    "graph.mutated", "policy.violation", "fault.injected", "halt.requested", "safe_stop", "run.halted",
    "node.changes_requested", "run.started", "run.resumed", "run.finished",
}


def _cell(text: object, limit: int = 110) -> str:
    s = str(text).replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def write_report(engine: "Engine") -> None:
    events = engine.audit.events()
    m = metrics_mod.compute(events)
    ctx = engine.context
    spec = ctx.get("spec", {}) or {}
    t0 = events[0]["ts"] if events else 0.0
    statuses = {n: s.status.value for n, s in engine.states.items()}

    out: list[str] = [
        f"# Run report: {engine.scenario['name']} ({engine.scenario.get('type')})", "",
        f"- **Run id:** `{engine.run_id}`",
        f"- **Status:** **{engine.status.value}**",
        f"- **Provider chain:** {engine.provider.name}",
        f"- **Faults injected:** {sorted(engine.faults) or 'none'}",
        f"- **End-to-end latency:** {m['end_to_end_latency_s']} s", "",
        "## Requirement (as given)", "", "```text", (ctx.get("requirement") or "").strip(), "```", "",
        "## Normalised spec", "",
        f"**Goal:** {spec.get('goal', '-')}", "",
        f"**Flags:** {spec.get('flags', [])}  |  **Vague terms detected:** "
        f"{spec.get('ambiguity', {}).get('vague_terms', [])}", "",
    ]
    for title, key in (("Functional", "functional"), ("Acceptance criteria", "acceptance_criteria"),
                       ("Assumptions / clarifications", "assumptions"), ("Out of scope", "out_of_scope")):
        if spec.get(key):
            out += [f"**{title}**", ""] + [f"- {_cell(x, 300)}" for x in spec[key]] + [""]

    plan = ctx.get("plan") or {}
    out += ["## Orchestration graph (final)", "", "```mermaid", engine.graph.to_mermaid(statuses), "```", "",
            f"Parallel waves: `{engine.graph.levels()}`", "",
            f"Critical path of implementation tasks: `{plan.get('critical_path', [])}`", ""]
    if plan.get("tasks"):
        out += ["### Task decomposition", "", "| Task | Title | Depends on | Scope | Risk | Proof (tests) |",
                "|---|---|---|---|---|---|"]
        out += [f"| {t['id']} | {_cell(t['title'])} | {', '.join(t['depends_on']) or '-'} | "
                f"{_cell(', '.join(t['write_scope']))} | {t['risk']} | {_cell(', '.join(t.get('targeted_tests', [])))} |"
                for t in plan["tasks"]]
        out.append("")

    out += ["## Execution timeline", "", "| t+s | Event | Node | Detail |", "|---|---|---|---|"]
    for e in events:
        if e["event"] not in INTERESTING:
            continue
        d = e["data"]
        detail = (d.get("error") or d.get("reason") or d.get("summary") or d.get("detail")
                  or (f"{d.get('decision')} by {d.get('approver')}: {d.get('comment', '')}" if e["event"] == "approval.decided" else "")
                  or (f"added={d.get('added')} updated={d.get('updated')} removed={d.get('removed')}" if e["event"] == "graph.mutated" else "")
                  or (f"files={d.get('files')}" if d.get("files") else "")
                  or (f"checkpoint={d.get('checkpoint')} reasons={d.get('reasons')}" if e["event"] == "approval.requested" else "")
                  or (f"status={d.get('status')}" if d.get("status") else ""))
        out.append(f"| {e['ts'] - t0:6.2f} | `{e['event']}` | {e['node'] or ''} | {_cell(detail, 160)} |")
    out.append("")

    out += ["## Human checkpoints", "", "| Checkpoint | Node | # | Decision | Approver | Comment |",
            "|---|---|---|---|---|---|"]
    out += [f"| {a['checkpoint']} | {a['node']} | {a['occurrence']} | **{a['decision']}** | {a['approver']} | "
            f"{_cell(a['comment'] or a.get('answers') or '')} |" for a in engine.approvals]
    out.append("")

    out += ["## Decisions and lineage", ""]
    for d in ctx.decisions.values():
        out.append(f"- **{d.id}** ({d.decided_by}, {d.node}): {_cell(d.summary, 240)}")
    if "release:readiness" in ctx.decisions:
        out += ["", "Lineage of the release decision:", "", "```text"]
        out += ctx.lineage("release:readiness")
        out += ["```"]
    out.append("")

    verification = ctx.get("verification") or {}
    security = ctx.get("security") or {}
    readiness = ctx.get("readiness") or {}
    out += ["## Validation", "",
            f"- Tests: {verification.get('passed', '-')} passed, {verification.get('failed', '-')} failed "
            f"({verification.get('duration_s', '-')} s)",
            f"- API contract: missing={verification.get('contract', {}).get('missing', '-')}, "
            f"undeclared={verification.get('contract', {}).get('undeclared', '-')}",
            f"- Security findings: {security.get('by_severity', '-')}",
            f"- Residual threats: {readiness.get('residual_risks', '-')}", ""]
    if readiness.get("checks"):
        out += ["| Readiness check | Result | Detail |", "|---|---|---|"]
        out += [f"| {c['name']} | {'PASS' if c['passed'] else 'FAIL'} | {_cell(c['detail'])} |" for c in readiness["checks"]]
        out.append("")

    out += ["## Reliability metrics", "", "| Metric | Value |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in m.items()]
    out += ["", "## Workspace commits (checkpoints)", "", "```text"]
    out += [f"{sha[:10]} {msg}" for sha, msg in engine.workspace.log()]
    out += ["```", "", f"Audit trail: `audit.jsonl` (hash-chained). Artifacts: `artifacts/`. Released tree: `release/`.", ""]
    (engine.run_dir / "report.md").write_text("\n".join(out))

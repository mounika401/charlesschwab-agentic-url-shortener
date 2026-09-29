"""Release readiness, release checkpoint and promotion (with rollback)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tarfile
import io
from pathlib import Path

from ..models import AgentResult, Decision
from .base import AgentContext, AgentError

SMOKE_SCRIPT = """
import sys
from fastapi.testclient import TestClient
from shortener.app import create_app
from shortener.config import Settings
from shortener.db import Database
client = TestClient(create_app(Settings(database_path=':memory:', base_url='http://smoke'), Database(':memory:')),
                    follow_redirects=False)
assert client.get('/healthz').status_code == 200, 'healthz'
created = client.post('/api/v1/links', json={'url': 'https://example.com/smoke'})
assert created.status_code == 201, f'create {created.status_code}'
code = created.json()['code']
hop = client.get('/' + code)
assert hop.status_code == 307 and hop.headers['location'] == 'https://example.com/smoke', 'redirect'
if len(sys.argv) > 1 and sys.argv[1] == 'fail':
    raise SystemExit('injected smoke failure')
print('smoke ok')
"""


def read_version(root: Path) -> str | None:
    init = root / "shortener" / "__init__.py"
    if not init.exists():
        return None
    match = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text())
    return match.group(1) if match else None


class ReadinessAgent:
    name = "release_readiness"

    def run(self, ctx: AgentContext) -> AgentResult:
        inp = ctx.inputs
        tasks = inp.get("plan", {}).get("tasks", [])
        verification = inp.get("verification", {})
        security = inp.get("security", {})
        baseline = inp.get("baseline", {})
        version = read_version(ctx.workspace.root)
        approvals = inp.get("approvals", [])

        def check(name: str, passed: bool, detail: str) -> dict:
            return {"name": name, "passed": bool(passed), "detail": detail}

        missing_tasks = [t["id"] for t in tasks if f"change:{t['id']}" not in inp]
        checks = [
            check("all_tasks_implemented", not missing_tasks, f"missing {missing_tasks}" if missing_tasks
                  else f"{len(tasks)} tasks"),
            check("tests_green", verification.get("ok") and verification.get("failed") == 0,
                  f"{verification.get('passed')} passed, {verification.get('failed')} failed"),
            check("api_contract", not verification.get("contract", {}).get("missing"),
                  f"missing={verification.get('contract', {}).get('missing')}"),
            check("no_high_security_findings", security.get("by_severity", {}).get("high", 1) == 0,
                  f"{security.get('by_severity')}"),
            check("docs_generated", bool(inp.get("docs", {}).get("api")), inp.get("docs", {}).get("api", "none")),
            check("version_bumped", version is not None and version != baseline.get("version"),
                  f"{baseline.get('version')} -> {version}"),
            check("no_rejected_checkpoints", not any(a["decision"] == "reject" for a in approvals),
                  f"{len(approvals)} checkpoint decisions so far"),
        ]
        residual = inp.get("threats", {}).get("residual", [])
        diffstat = ctx.workspace.diff(baseline.get("commit", "HEAD"), stat=True).strip() if baseline.get("commit") else ""

        notes = [f"# Release notes: {ctx.scenario['name']} {version}", "", inp.get("spec", {}).get("goal", ""), "",
                 "## Readiness checklist", ""]
        notes += [f"- [{'x' if c['passed'] else ' '}] {c['name']}: {c['detail']}" for c in checks]
        notes += ["", "## Residual risks (accepted, not blocking)", ""]
        notes += [f"- {t}" for t in residual] or ["- none"]
        notes += ["", "## Change summary", "", "```", diffstat or "(no diff)", "```"]
        (ctx.artifacts_dir / "release-notes.md").write_text("\n".join(notes) + "\n")

        ready = all(c["passed"] for c in checks)
        summary = "\n".join(f"[{'PASS' if c['passed'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in checks)
        summary += f"\nresidual risks: {residual or 'none'}\n{diffstat}"
        return AgentResult(
            outputs={"readiness": {"checks": checks, "ready": ready, "version": version,
                                   "residual_risks": residual, "summary": summary}},
            decisions=[Decision(id="release:readiness", node=ctx.node.id,
                                summary=f"ready={ready} version={version}",
                                based_on=["verification", "security", "docs", "plan"])],
            summary=summary,
        )


class CheckpointAgent:
    """No-op work unit whose only purpose is to host a human approval checkpoint."""

    name = "checkpoint"

    def run(self, ctx: AgentContext) -> AgentResult:
        readiness = ctx.inputs.get("readiness", {})
        return AgentResult(outputs={}, summary=readiness.get("summary", "approval checkpoint"))


class ReleaseAgent:
    """Promotes the approved workspace commit, smoke-tests it, rolls back on failure."""

    name = "release"

    def run(self, ctx: AgentContext) -> AgentResult:
        target = Path(ctx.extra["release_dir"])
        previous = target.with_name(target.name + ".previous")
        head = ctx.workspace.head()

        if previous.exists():
            shutil.rmtree(previous)
        if target.exists():
            target.rename(previous)
        archive = subprocess.run(["git", "archive", "--format=tar", head], cwd=ctx.workspace.root,
                                 capture_output=True, check=True).stdout
        target.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(target, filter="data")

        args = [sys.executable, "-c", SMOKE_SCRIPT] + (["fail"] if "smoke_failure" in ctx.extra.get("faults", ()) else [])
        proc = subprocess.run(args, cwd=target, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            shutil.rmtree(target)
            restored = previous.exists()
            if restored:
                previous.rename(target)
            ctx.audit("rollback.performed", node=ctx.node.id, scope="release",
                      detail="promotion reverted after failed smoke test", restored_previous=restored)
            raise AgentError(f"smoke test failed, promotion rolled back: {(proc.stderr or proc.stdout).strip()[-300:]}")
        if previous.exists():
            shutil.rmtree(previous)
        return AgentResult(
            outputs={"release": {"commit": head, "path": str(target), "smoke_passed": True,
                                 "version": read_version(target)}},
            decisions=[Decision(id="release:promoted", node=ctx.node.id,
                                summary=f"promoted {head[:10]} to {target.name}",
                                based_on=["release:readiness"])],
            summary=f"released {read_version(target)} ({head[:10]})",
        )

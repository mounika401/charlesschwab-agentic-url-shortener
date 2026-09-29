"""Validation agents: full test verification, security scan, API docs."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from typing import Any

from ..gates import run_pytest
from ..models import AgentResult, Decision
from .base import AgentContext, AgentError

OPENAPI_SCRIPT = (
    "import json; from shortener.app import create_app; from shortener.config import Settings; "
    "from shortener.db import Database; "
    "print(json.dumps(create_app(Settings(database_path=':memory:'), Database(':memory:')).openapi()))"
)


def load_openapi(ctx: AgentContext) -> dict[str, Any]:
    proc = subprocess.run([sys.executable, "-c", OPENAPI_SCRIPT], cwd=ctx.workspace.root,
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AgentError(f"could not import application to read OpenAPI: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)


def implemented_endpoints(openapi: dict[str, Any]) -> set[tuple[str, str]]:
    return {(m.upper(), p) for p, ops in openapi.get("paths", {}).items() for m in ops}


class VerifyAgent:
    """Runs the whole suite + checks the implemented API against the design contract."""

    name = "verify"

    def run(self, ctx: AgentContext) -> AgentResult:
        started = time.time()
        ok, tail = run_pytest(ctx, ["tests"], timeout=int(ctx.node.params.get("timeout", 600)))
        passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", tail)) else 0
        failed = sum(int(n) for n in re.findall(r"(\d+) (?:failed|errors?)\b", tail))
        if not ok and failed == 0:
            failed = 1  # non-zero exit without a parsed count (collection error, timeout)

        designed = {(e["method"].upper(), e["path"]) for e in ctx.inputs.get("design", {}).get("endpoints", [])}
        actual = implemented_endpoints(load_openapi(ctx))
        contract = {"missing": sorted(f"{m} {p}" for m, p in designed - actual),
                    "undeclared": sorted(f"{m} {p}" for m, p in actual - designed)}
        verification = {"passed": passed, "failed": failed, "ok": ok,
                        "duration_s": round(time.time() - started, 2), "contract": contract, "tail": tail}
        return AgentResult(
            outputs={"verification": verification},
            decisions=[Decision(id="verify:result", node=ctx.node.id,
                                summary=f"{passed} passed / {verification['failed']} failed; "
                                        f"contract missing={contract['missing']}",
                                based_on=[n for n in ctx.node.depends_on])],
            summary=f"{passed} tests passed, {verification['failed']} failed",
        )


class SecurityAgent:
    name = "security"

    def run(self, ctx: AgentContext) -> AgentResult:
        include = ctx.node.params.get("include", ["shortener/**", "tests/**", "requirements.txt"])
        findings = [f.to_dict() for f in ctx.policy.scan_tree(ctx.workspace.root, include)]
        by_sev = {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")}
        lines = ["# Security scan", "", f"Findings by severity: {by_sev}", ""]
        lines += [f"- [{f['severity']}] {f['rule']} {f['path']}:{f['line']} `{f['snippet']}`" for f in findings]
        (ctx.artifacts_dir / "security-scan.md").write_text("\n".join(lines) + "\n")
        return AgentResult(
            outputs={"security": {"findings": findings, "by_severity": by_sev}},
            decisions=[Decision(id="security:scan", node=ctx.node.id, summary=f"findings {by_sev}",
                                rationale="policy.yaml secret + forbidden-code rules over product code",
                                based_on=[n for n in ctx.node.depends_on])],
            summary=f"findings {by_sev}",
        )


class DocsAgent:
    """Generates API reference from the live OpenAPI schema and a changelog entry."""

    name = "docs"

    def run(self, ctx: AgentContext) -> AgentResult:
        openapi = load_openapi(ctx)
        spec = ctx.inputs["spec"]
        plan = ctx.inputs.get("plan", {})
        version = openapi.get("info", {}).get("version", "0.0.0")

        lines = [f"# {openapi['info']['title']} API reference", "",
                 f"Version {version}. Generated from the application's OpenAPI schema by the docs agent; "
                 "do not edit by hand.", ""]
        for path, ops in sorted(openapi["paths"].items()):
            for method, op in ops.items():
                lines += [f"## {method.upper()} `{path}`", ""]
                if op.get("summary"):
                    lines += [op["summary"], ""]
                params = op.get("parameters", [])
                if params:
                    lines += ["Parameters: " + ", ".join(f"`{p['name']}` ({p['in']})" for p in params), ""]
                body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
                if body.get("$ref"):
                    schema_name = body["$ref"].split("/")[-1]
                    fields = openapi["components"]["schemas"][schema_name].get("properties", {})
                    lines += [f"Body `{schema_name}`: " + ", ".join(f"`{f}`" for f in fields), ""]
                lines += ["Responses: " + ", ".join(sorted(op.get("responses", {}))), ""]
        api_path = ctx.write("docs/API.md", "\n".join(lines))

        changelog = ctx.workspace.root / "CHANGELOG.md"
        existing = changelog.read_text() if changelog.exists() else "# Changelog\n"
        header = f"## {version} - {ctx.scenario['name']}"
        entry = "\n".join([header, "", spec.get("goal", ""), ""] + [f"- {t['title']}" for t in plan.get("tasks", [])])
        if header in existing:
            # Re-run after a re-plan: replace this version's section so it reflects the final plan.
            start = existing.index(header)
            nxt = existing.find("\n## ", start + len(header))
            existing = existing[:start] + entry + ("\n" + existing[nxt:] if nxt != -1 else "\n")
        else:
            head, _, rest = existing.partition("\n")
            existing = f"{head}\n\n" + entry + "\n" + rest
        ctx.write("CHANGELOG.md", existing.rstrip() + "\n")

        designed = {f"{e['method'].upper()} {e['path']}" for e in ctx.inputs.get("design", {}).get("endpoints", [])}
        documented = {f"{m} {p}" for m, p in implemented_endpoints(openapi)}
        return AgentResult(
            outputs={"docs": {"api": api_path, "changelog": "CHANGELOG.md", "version": version,
                              "undocumented": sorted(designed - documented)}},
            artifacts=[api_path, "CHANGELOG.md"],
            summary=f"API reference for {len(documented)} endpoints; changelog {version}",
        )

"""Codebase reasoning agent (brownfield).

Real static analysis of the workspace, not a scripted response: it parses every
module with ``ast`` to build the import graph, HTTP routes, SQL tables and the
test-to-module map, then computes which modules a requirement impacts and their
blast radius (everything that transitively imports them).
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from ..models import AgentResult, Decision
from .base import AgentContext

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def identifier_tokens(name: str) -> set[str]:
    """``generate_code`` -> {generate, code}; ``LinkService`` -> {link, service}.

    Whole-token matching stops 'rate' from matching 'generate'.
    """
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return {t.lower() for t in re.split(r"[_\W]+", spaced) if t}


def analyse(root: Path, package: str) -> dict:
    modules: dict[str, dict] = {}
    imports: dict[str, set[str]] = defaultdict(set)
    routes: list[dict] = []
    tables: dict[str, str] = {}
    tests: dict[str, set[str]] = defaultdict(set)

    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in rel or not (rel.startswith(f"{package}/") or rel.startswith("tests/")):
            continue
        source = path.read_text()
        tree = ast.parse(source, filename=rel)
        name = module_name(path, root)
        is_test = rel.startswith("tests/")
        deps: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    base = name.rsplit(".", node.level)[0] if not rel.endswith("__init__.py") else name
                    target = f"{base}.{node.module}" if node.module else base
                    deps.add(target)
                    deps.update(f"{base}.{a.name}" for a in node.names if not node.module)
                elif node.module and node.module.split(".")[0] == package:
                    deps.add(node.module)
            elif isinstance(node, ast.Import):
                deps.update(a.name for a in node.names if a.name.split(".")[0] == package)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not is_test:
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                            and dec.func.attr in HTTP_METHODS and dec.args
                            and isinstance(dec.args[0], ast.Constant)):
                        routes.append({"method": dec.func.attr.upper(), "path": dec.args[0].value,
                                       "handler": node.name, "module": name})
        for match in re.finditer(r"CREATE TABLE (\w+)", source):
            tables[match.group(1)] = name
        if is_test:
            for dep in deps:
                tests[dep].add(rel)
            continue
        defs = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))]
        modules[name] = {"path": rel, "lines": source.count("\n") + 1, "definitions": defs}
        imports[name] = {d for d in deps if d != name}

    reverse: dict[str, set[str]] = defaultdict(set)
    for mod, deps in imports.items():
        for dep in deps:
            reverse[dep].add(mod)
    return {"modules": modules, "imports": {k: sorted(v) for k, v in imports.items()},
            "reverse": reverse, "routes": routes, "tables": tables,
            "tests": {k: sorted(v) for k, v in tests.items()}}


class CodebaseAgent:
    name = "codebase"

    def run(self, ctx: AgentContext) -> AgentResult:
        package = ctx.node.params.get("package", "shortener")
        spec = ctx.inputs.get("spec", {})
        info = analyse(ctx.workspace.root, package)
        keywords = [k.lower() for k in spec.get("keywords", [])]

        # Direct impact: modules the spec names, plus modules that *define* a
        # symbol (function/class/route handler) matching a spec keyword.
        # Matching definitions rather than raw text keeps comments and
        # incidental mentions from inflating the blast radius.
        impacted: dict[str, list[str]] = {}
        handlers = defaultdict(list)
        for route in info["routes"]:
            handlers[route["module"]].append(route["handler"])
        for mod, meta in info["modules"].items():
            reasons = []
            if mod in spec.get("touches", []):
                reasons.append("named in spec")
            tokens = {tok for n in meta["definitions"] + handlers[mod] for tok in identifier_tokens(n)}
            hits = sorted(k for k in keywords if k in tokens)
            if hits:
                reasons.append(f"defines symbols matching {hits}")
            if reasons:
                impacted[mod] = reasons

        blast: set[str] = set()
        stack = list(impacted)
        while stack:
            for parent in info["reverse"].get(stack.pop(), set()):
                if parent not in blast and parent not in impacted:
                    blast.add(parent)
                    stack.append(parent)

        affected = {t for mod in list(impacted) + sorted(blast) for t in info["tests"].get(mod, [])}
        # A shared conftest fixture reaches every test in its directory.
        for conftest in [t for t in affected if t.endswith("conftest.py")]:
            folder = conftest.rsplit("/", 1)[0]
            affected |= {p.relative_to(ctx.workspace.root).as_posix()
                         for p in (ctx.workspace.root / folder).glob("test_*.py")}
        affected_tests = sorted(affected)
        risk_notes = []
        if any(t for t, m in info["tables"].items() if m in impacted):
            risk_notes.append("persistence layer impacted: schema change needs a forward-only migration")
        if f"{package}.app" in impacted or f"{package}.app" in blast:
            risk_notes.append("HTTP layer in blast radius: public API contract must stay backward compatible")

        report = [f"# Codebase analysis ({package})", "", "## Modules", ""]
        report += [f"- `{m}` ({v['lines']} lines): {', '.join(v['definitions'][:8])}" for m, v in info["modules"].items()]
        report += ["", "## Routes", ""] + [f"- {r['method']} {r['path']} -> {r['handler']}" for r in info["routes"]]
        report += ["", "## Impacted", ""] + [f"- `{m}`: {'; '.join(r)}" for m, r in impacted.items()]
        report += ["", "## Blast radius (transitive importers)", ""] + [f"- `{m}`" for m in sorted(blast)]
        report += ["", "## Tests to re-run", ""] + [f"- {t}" for t in affected_tests]
        (ctx.artifacts_dir / "codebase-analysis.md").write_text("\n".join(report) + "\n")

        codebase = {
            "modules": sorted(info["modules"]),
            "imports": info["imports"],
            "routes": info["routes"],
            "tables": info["tables"],
            "impacted": {m: r for m, r in impacted.items()},
            "blast_radius": sorted(blast),
            "affected_tests": affected_tests,
            "risk_notes": risk_notes,
        }
        return AgentResult(
            outputs={"codebase": codebase},
            decisions=[Decision(
                id="codebase:impact",
                node=ctx.node.id,
                summary=f"impacted {sorted(impacted)}; blast radius {sorted(blast)}",
                rationale="AST import graph + keyword/touch matching against the normalised spec",
                based_on=["spec"],
            )],
            summary=f"{len(info['modules'])} modules, {len(info['routes'])} routes, {len(impacted)} impacted",
        )

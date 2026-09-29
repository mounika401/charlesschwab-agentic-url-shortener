"""Policy guardrails: security, compliance and change control.

The policy engine is deliberately separate from agents. Agents propose; the
policy engine (driven by ``governance/policy.yaml``) decides whether a proposal
may land and whether a human has to see it first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .graph import in_scope
from .models import NodeSpec, PolicyViolation, Risk

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class FileChange:
    path: str
    kind: str                     # added | modified | deleted
    added_lines: list[str] = field(default_factory=list)
    removed_count: int = 0

    @property
    def lines_changed(self) -> int:
        return len(self.added_lines) + self.removed_count


@dataclass
class Finding:
    rule: str
    severity: str
    path: str
    line: int
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Policy:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        cc = config["change_control"]
        sec = config["security"]
        self.protected_paths: list[str] = cc["protected_paths"]
        self.max_files = int(cc["max_files_per_change"])
        self.max_lines = int(cc["max_lines_per_change"])
        self.migration_paths: list[str] = cc["migration_paths"]
        self.dependency_files: list[str] = cc["dependency_files"]
        self.public_api_paths: list[str] = cc["public_api_paths"]
        self.secret_rules = [(r["name"], re.compile(r["regex"])) for r in sec["secret_patterns"]]
        self.code_rules = [(r["name"], r["severity"], re.compile(r["regex"])) for r in sec["forbidden_code"]]
        self.dependency_allowlist = {d.lower() for d in sec["dependency_allowlist"]}
        self.pii_columns: list[str] = config["compliance"]["forbidden_pii_columns"]
        self.max_auto_risk = Risk(config["autonomy"]["max_auto_approve_risk"])
        self.always_required = set(config["approvals"]["always_required"])
        self.approval_actions = set(config["approvals"]["actions_requiring_approval"])

    @classmethod
    def load(cls, path: Path) -> "Policy":
        return cls(yaml.safe_load(path.read_text()))

    # ---- approval routing ---------------------------------------------------------
    def approval_reasons(self, node: NodeSpec, actions: set[str]) -> list[str]:
        reasons = []
        if node.approval:
            reasons.append(f"checkpoint '{node.approval}' is mandatory for this stage")
        if node.risk.rank > self.max_auto_risk.rank:
            reasons.append(f"node risk '{node.risk.value}' exceeds autonomy limit '{self.max_auto_risk.value}'")
        for action in sorted(actions & self.approval_actions):
            reasons.append(f"change set contains high-impact action '{action}'")
        return reasons

    def can_auto_approve(self, checkpoint: str) -> bool:
        return checkpoint not in self.always_required

    # ---- change classification ----------------------------------------------------
    def classify(self, changes: list[FileChange]) -> set[str]:
        actions: set[str] = set()
        for ch in changes:
            if ch.kind == "deleted":
                actions.add("deletion")
            if in_scope(ch.path, self.migration_paths) and any(
                re.search(r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX)\b", line, re.I) for line in ch.added_lines
            ):
                actions.add("schema_migration")
            if in_scope(ch.path, self.dependency_files):
                actions.add("new_dependency")
            if in_scope(ch.path, self.public_api_paths):
                actions.add("public_api_change")
        return actions

    # ---- guardrails on a proposed change set --------------------------------------
    def enforce_change_set(self, node: NodeSpec, changes: list[FileChange]) -> list[Finding]:
        """Raise PolicyViolation for hard breaches; return non-blocking findings."""
        for ch in changes:
            if in_scope(ch.path, self.protected_paths):
                raise PolicyViolation("change_control.protected_path", f"{node.id} modified {ch.path}")
            if node.write_scope and not in_scope(ch.path, node.write_scope):
                raise PolicyViolation(
                    "change_control.write_scope", f"{node.id} wrote {ch.path} outside scope {node.write_scope}"
                )
        if len(changes) > self.max_files:
            raise PolicyViolation("change_control.max_files", f"{len(changes)} files > {self.max_files}")
        total = sum(c.lines_changed for c in changes)
        if total > self.max_lines:
            raise PolicyViolation("change_control.max_lines", f"{total} lines > {self.max_lines}")

        findings = self.scan_lines(
            [(ch.path, i + 1, line) for ch in changes for i, line in enumerate(ch.added_lines)]
        )
        blocking = [f for f in findings if f.severity == "high"]
        if blocking:
            f = blocking[0]
            raise PolicyViolation(f"security.{f.rule}", f"{f.path}:{f.line}: {f.snippet}")

        for ch in changes:
            if in_scope(ch.path, self.migration_paths):
                for line in ch.added_lines:
                    for col in self.pii_columns:
                        if re.search(rf"\b{col}\b\s+(TEXT|INTEGER|BLOB|VARCHAR|REAL)", line, re.I):
                            raise PolicyViolation("compliance.pii_column", f"{ch.path}: column '{col}'")
            if in_scope(ch.path, ["requirements.txt"]):
                for line in ch.added_lines:
                    name = re.split(r"[<>=\[~! ;]", line.strip(), maxsplit=1)[0].lower()
                    if name and not name.startswith("#") and name not in self.dependency_allowlist:
                        raise PolicyViolation("security.dependency_allowlist", f"'{name}' is not allowlisted")
        return findings

    def scan_lines(self, lines: list[tuple[str, int, str]]) -> list[Finding]:
        findings: list[Finding] = []
        for path, lineno, line in lines:
            is_test = path.startswith("tests/")
            for name, rx in self.secret_rules:
                if rx.search(line):
                    findings.append(Finding(f"secret.{name}", "high", path, lineno, line.strip()[:120]))
            for name, severity, rx in self.code_rules:
                if rx.search(line):
                    # Tests may legitimately print or use fixtures; downgrade there.
                    sev = "low" if is_test and severity != "high" else severity
                    findings.append(Finding(f"code.{name}", sev, path, lineno, line.strip()[:120]))
        return findings

    def scan_tree(self, root: Path, include: list[str]) -> list[Finding]:
        lines: list[tuple[str, int, str]] = []
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_file() and in_scope(rel, include) and "__pycache__" not in rel:
                for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                    lines.append((rel, i, line))
        return self.scan_lines(lines)

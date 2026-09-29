"""Git-backed workspace with scoped writes, checkpoints and rollback.

Every successful node that changes files produces a commit (a checkpoint).
Uncommitted edits belong to whichever running node owns that path scope, so a
failed attempt can be rolled back *without* disturbing sibling nodes running
in parallel on disjoint scopes.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from .graph import in_scope
from .models import PolicyViolation
from .policy import FileChange

IGNORE = "__pycache__/\n.pytest_cache/\n*.pyc\n*.db\n.orchestrator/\n"


class WorkspaceError(RuntimeError):
    pass


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()

    # ---- git plumbing ---------------------------------------------------------------
    def git(self, *args: str, check: bool = True, input_text: str | None = None) -> str:
        with self._lock:
            proc = subprocess.run(
                ["git", *args], cwd=self.root, capture_output=True, text=True, input=input_text
            )
        if check and proc.returncode != 0:
            raise WorkspaceError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout

    def init(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "orchestrator@agentic.local")
        self.git("config", "user.name", "agentic-orchestrator")
        (self.root / ".gitignore").write_text(IGNORE)
        return self.checkpoint("baseline: empty workspace")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def checkpoint(self, message: str, scope: list[str] | None = None) -> str:
        """Commit pending changes (optionally only within ``scope``); return the commit id."""
        with self._lock:
            paths = [c.path for c in self.pending_changes(scope)] if scope is not None else None
            if paths is not None and not paths:
                return self.head()
            if paths is None:
                self.git("add", "-A")
            else:
                self.git("add", "-A", "--", *paths)
            self.git("commit", "-q", "--allow-empty", "-m", message)
            return self.head()

    # ---- inspection -----------------------------------------------------------------
    def pending_changes(self, scope: list[str] | None = None) -> list[FileChange]:
        """Uncommitted changes, optionally filtered to a path scope."""
        with self._lock:
            status = self.git("status", "--porcelain", "--untracked-files=all")
            changes: list[FileChange] = []
            for line in status.splitlines():
                code, path = line[:2], line[3:].strip().strip('"')
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                if scope is not None and not in_scope(path, scope):
                    continue
                if code == "??":
                    content = (self.root / path).read_text(errors="replace").splitlines()
                    changes.append(FileChange(path, "added", content, 0))
                elif "D" in code:
                    old = self.git("show", f"HEAD:{path}", check=False).splitlines()
                    changes.append(FileChange(path, "deleted", [], len(old)))
                else:
                    diff = self.git("diff", "HEAD", "--unified=0", "--", path)
                    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
                    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
                    changes.append(FileChange(path, "modified", added, removed))
            return changes

    def diff(self, base: str, head: str = "HEAD", stat: bool = False) -> str:
        args = ["diff", "--stat"] if stat else ["diff"]
        return self.git(*args, base, head)

    def files(self) -> list[str]:
        return sorted(self.git("ls-files").split())

    def log(self, base: str | None = None) -> list[tuple[str, str]]:
        rng = [f"{base}..HEAD"] if base else []
        out = self.git("log", "--format=%H %s", "--reverse", *rng)
        return [tuple(l.split(" ", 1)) for l in out.splitlines() if l]  # type: ignore[misc]

    # ---- scoped writes --------------------------------------------------------------
    def write_file(self, rel_path: str, content: str, scope: list[str]) -> None:
        self._check_scope(rel_path, scope)
        target = self.root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def apply_patch(self, patch: str, scope: list[str]) -> list[str]:
        touched = sorted({
            line.split(" b/", 1)[1].strip()
            for line in patch.splitlines()
            if line.startswith("diff --git ")
        })
        for path in touched:
            self._check_scope(path, scope)
        with self._lock:
            proc = subprocess.run(["git", "apply", "--check", "-"], cwd=self.root, input=patch,
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                raise WorkspaceError(f"patch does not apply: {proc.stderr.strip()}")
            self.git("apply", "-", input_text=patch)
        return touched

    def _check_scope(self, rel_path: str, scope: list[str]) -> None:
        if rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise PolicyViolation("change_control.path_traversal", rel_path)
        if not in_scope(rel_path, scope):
            raise PolicyViolation("change_control.write_scope", f"{rel_path} is outside scope {scope}")

    # ---- rollback -------------------------------------------------------------------
    def discard(self, scope: list[str]) -> list[str]:
        """Roll back uncommitted changes inside ``scope`` only."""
        with self._lock:
            discarded = []
            for ch in self.pending_changes(scope):
                if ch.kind == "added":
                    (self.root / ch.path).unlink(missing_ok=True)
                else:
                    self.git("checkout", "HEAD", "--", ch.path)
                discarded.append(ch.path)
            return discarded

    def reset_to(self, commit: str) -> None:
        """Hard rollback of the whole workspace to a checkpoint."""
        with self._lock:
            self.git("reset", "-q", "--hard", commit)
            self.git("clean", "-q", "-fd")

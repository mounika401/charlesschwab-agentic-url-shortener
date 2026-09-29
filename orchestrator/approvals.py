"""Human approval checkpoints.

The engine never decides a checkpoint itself. It builds an ``ApprovalRequest``
explaining *why* a human is needed (policy reasons, risk, what changed) and
hands it to an ``Approver``:

* ``InteractiveApprover`` prompts on the terminal (the real human-in-the-loop).
* ``ScriptedApprover`` replays decisions recorded in a YAML file, so demos and
  CI runs are reproducible. Each decision is still attributed to the named
  human who recorded it, and an unmatched request falls back to the policy
  default (``reject``) - silence never means consent.
"""

from __future__ import annotations

import fnmatch
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

APPROVE, REJECT, REQUEST_CHANGES, ANSWER = "approve", "reject", "request_changes", "answer"


@dataclass
class ApprovalRequest:
    checkpoint: str
    node: str
    title: str
    reasons: list[str]
    summary: str
    risk: str
    occurrence: int = 1
    questions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ApprovalDecision:
    decision: str
    approver: str
    comment: str = ""
    answers: dict[str, Any] = field(default_factory=dict)
    amendment: dict[str, Any] = field(default_factory=dict)


class Approver(Protocol):
    def decide(self, request: ApprovalRequest) -> ApprovalDecision: ...


class ScriptedApprover:
    def __init__(self, path: Path, default: str = REJECT) -> None:
        data = yaml.safe_load(path.read_text()) or {}
        self.approver = data.get("approver", "unknown-human")
        self.rules: list[dict[str, Any]] = data.get("decisions", [])
        self.default = default
        self._used: set[int] = set()
        self._lock = threading.Lock()

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        with self._lock:
            for idx, rule in enumerate(self.rules):
                if idx in self._used:
                    continue
                match = rule.get("match", {})
                if match.get("checkpoint", request.checkpoint) != request.checkpoint:
                    continue
                if not fnmatch.fnmatch(request.node, match.get("node", "*")):
                    continue
                if "occurrence" in match and match["occurrence"] != request.occurrence:
                    continue
                if rule.get("once", True):
                    self._used.add(idx)
                return ApprovalDecision(
                    decision=rule["decision"],
                    approver=f"{self.approver} (recorded)",
                    comment=rule.get("comment", ""),
                    answers=rule.get("answers", {}),
                    amendment=rule.get("amendment", {}),
                )
        return ApprovalDecision(self.default, "policy-default", f"no recorded decision for '{request.checkpoint}'")


class InteractiveApprover:
    """Terminal prompt. Serialised so parallel nodes never interleave prompts."""

    _lock = threading.Lock()

    def __init__(self, name: str | None = None, stream=sys.stdin) -> None:
        self.name = name or "operator"
        self.stream = stream

    def _ask(self, prompt: str) -> str:
        print(prompt, end="", flush=True)
        return (self.stream.readline() or "").strip()

    def decide(self, request: ApprovalRequest) -> ApprovalDecision:
        with self._lock:
            print("\n" + "=" * 78)
            print(f"APPROVAL REQUIRED  [{request.checkpoint}]  node={request.node}  risk={request.risk}")
            print(request.title)
            for reason in request.reasons:
                print(f"  - why: {reason}")
            print("-" * 78)
            print(request.summary)
            print("=" * 78)
            if request.questions:
                answers = {}
                for q in request.questions:
                    default = q.get("default")
                    reply = self._ask(f"{q['question']} [{default}]: ")
                    answers[q["id"]] = reply or default
                return ApprovalDecision(ANSWER, self.name, "answered interactively", answers=answers)
            choice = self._ask("approve / reject / changes? [a/r/c]: ").lower()
            if choice.startswith("a"):
                return ApprovalDecision(APPROVE, self.name, self._ask("comment (optional): "))
            if choice.startswith("c"):
                comment = self._ask("describe the change you want: ")
                return ApprovalDecision(REQUEST_CHANGES, self.name, comment,
                                        amendment={"requirement": comment, "target": "requirements"})
            return ApprovalDecision(REJECT, self.name, self._ask("reason: ") or "rejected")

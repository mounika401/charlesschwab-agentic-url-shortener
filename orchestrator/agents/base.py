"""Agent interface.

An agent performs one kind of SDLC work. It receives an immutable snapshot of
the context it declared as inputs, a scoped workspace and a provider, and
returns outputs + decisions. It cannot approve itself, commit, or touch shared
state: those are engine and policy responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ..approvals import ApprovalDecision, ApprovalRequest
from ..models import AgentResult, NodeSpec
from ..policy import Policy
from ..providers import Provider
from ..workspace import Workspace


class AgentError(RuntimeError):
    """Recoverable agent failure: the engine may retry with this as feedback."""


@dataclass
class AgentContext:
    node: NodeSpec
    inputs: dict[str, Any]
    workspace: Workspace
    provider: Provider
    policy: Policy
    attempt: int
    feedback: list[str]
    artifacts_dir: Path
    scenario: dict[str, Any]
    audit: Callable[..., Any]
    ask_human: Callable[[ApprovalRequest], ApprovalDecision]
    extra: dict[str, Any] = field(default_factory=dict)

    def write(self, rel_path: str, content: str) -> str:
        self.workspace.write_file(rel_path, content, self.node.write_scope)
        return rel_path

    @property
    def flags(self) -> list[str]:
        return list(self.inputs.get("spec", {}).get("flags", []))


class Agent(Protocol):
    name: str

    def run(self, ctx: AgentContext) -> AgentResult: ...

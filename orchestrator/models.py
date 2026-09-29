"""Core data model for the orchestration layer.

Everything the engine persists is a plain dataclass that round-trips through
JSON, so a run can be stopped at any point and resumed from ``state.json``.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


class NodeStatus(str, enum.Enum):
    PENDING = "pending"            # waiting on dependencies
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"              # exhausted retries + fallback
    STALE = "stale"                # upstream output changed; must re-run (re-plan)
    SKIPPED = "skipped"            # removed by re-plan or not applicable
    BLOCKED = "blocked"            # a dependency failed

    @property
    def terminal(self) -> bool:
        return self in {NodeStatus.SUCCEEDED, NodeStatus.FAILED, NodeStatus.SKIPPED, NodeStatus.BLOCKED}


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SAFE_STOPPED = "safe_stopped"  # halted deliberately; workspace restored to last good checkpoint


class Risk(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2}[self.value]


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.0   # kept at 0 in demos; real providers would use jittered backoff


@dataclass
class NodeSpec:
    """Static definition of one unit of work in the dependency graph."""

    id: str
    stage: str
    agent: str
    depends_on: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    entry_gates: list[str] = field(default_factory=list)
    exit_gates: list[str] = field(default_factory=list)
    # Paths (globs, relative to the workspace) this node may modify. Nodes with
    # overlapping scopes are never scheduled concurrently.
    write_scope: list[str] = field(default_factory=list)
    risk: Risk = Risk.LOW
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    fallback_agent: str | None = None
    # Named approval checkpoint raised after the node's work passes its exit
    # gates and before its changes are committed. None = no mandatory checkpoint
    # (policy may still add one based on risk or detected high-impact actions).
    approval: str | None = None
    # How to re-run after upstream changes. "overwrite": regenerate on top of the
    # current workspace (documents). "rewind": roll the workspace back to before
    # this node's checkpoint first (code patches that assume a specific base).
    rerun_strategy: str = "overwrite"
    dynamic: bool = False          # created at runtime by the planner

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeSpec":
        data = dict(data)
        data["risk"] = Risk(data.get("risk", "low"))
        data["retry"] = RetryPolicy(**data.get("retry", {}))
        return cls(**data)


@dataclass
class NodeState:
    status: NodeStatus = NodeStatus.PENDING
    attempts: int = 0
    runs: int = 0                          # how many times the node has (re)started
    output: dict[str, Any] = field(default_factory=dict)
    output_hash: str | None = None
    input_fingerprint: str | None = None   # fingerprint of inputs when output was produced
    checkpoint: str | None = None          # workspace commit created by this node
    started_at: float | None = None
    ended_at: float | None = None
    error: str | None = None
    used_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeState":
        data = dict(data)
        data["status"] = NodeStatus(data["status"])
        return cls(**data)


@dataclass
class Decision:
    """A recorded engineering decision with its lineage."""

    id: str
    node: str
    summary: str
    rationale: str = ""
    alternatives: list[str] = field(default_factory=list)
    based_on: list[str] = field(default_factory=list)   # context keys / decision ids it depends on
    decided_by: str = "agent"                            # agent | human:<name> | policy


@dataclass
class AgentResult:
    outputs: dict[str, Any] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)   # workspace-relative paths written
    summary: str = ""


class GateFailure(Exception):
    """Raised when an entry or exit gate rejects a node's input or output."""

    def __init__(self, gate: str, reason: str) -> None:
        super().__init__(f"gate '{gate}' failed: {reason}")
        self.gate = gate
        self.reason = reason


class PolicyViolation(Exception):
    """A guardrail breach. Violations are never retried: they trigger safe-stop."""

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"policy violation [{rule}]: {detail}")
        self.rule = rule
        self.detail = detail


class ApprovalRejected(Exception):
    def __init__(self, checkpoint: str, approver: str, comment: str) -> None:
        super().__init__(f"approval '{checkpoint}' rejected by {approver}: {comment}")
        self.checkpoint = checkpoint
        self.approver = approver
        self.comment = comment


class ChangesRequested(Exception):
    """Human asked for changes at a checkpoint: triggers a re-plan, not a failure."""

    def __init__(self, checkpoint: str, approver: str, comment: str, amendment: dict[str, Any]) -> None:
        super().__init__(f"changes requested at '{checkpoint}' by {approver}: {comment}")
        self.checkpoint = checkpoint
        self.approver = approver
        self.comment = comment
        self.amendment = amendment


def stable_hash(value: Any) -> str:
    """Deterministic content hash used for output hashing and input fingerprints."""
    payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]

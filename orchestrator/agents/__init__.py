"""Agent registry."""

from .base import Agent, AgentContext, AgentError
from .codebase import CodebaseAgent
from .design import DesignAgent, ThreatModelAgent
from .implement import ImplementAgent
from .planner import PlannerAgent
from .release import CheckpointAgent, ReadinessAgent, ReleaseAgent
from .requirements import RequirementsAgent
from .validation import DocsAgent, SecurityAgent, VerifyAgent

REGISTRY: dict[str, Agent] = {
    agent.name: agent
    for agent in (
        RequirementsAgent(), CodebaseAgent(), DesignAgent(), ThreatModelAgent(), PlannerAgent(),
        ImplementAgent(), VerifyAgent(), SecurityAgent(), DocsAgent(), ReadinessAgent(),
        CheckpointAgent(), ReleaseAgent(),
    )
}

__all__ = ["REGISTRY", "Agent", "AgentContext", "AgentError"]

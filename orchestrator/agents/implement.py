"""Implementation agent: applies one planned task as a scoped change set."""

from __future__ import annotations

from ..models import AgentResult, Decision
from ..workspace import WorkspaceError
from .base import AgentContext, AgentError


class ImplementAgent:
    name = "implement"

    def run(self, ctx: AgentContext) -> AgentResult:
        task = ctx.node.params["task"]
        response = ctx.provider.generate("implement", {
            "task": task, "attempt": ctx.attempt, "feedback": ctx.feedback,
            "spec": ctx.inputs.get("spec"), "flags": ctx.flags,
        })
        written: list[str] = []
        if "patch" in response:
            try:
                written = ctx.workspace.apply_patch(response["patch"], ctx.node.write_scope)
            except WorkspaceError as exc:
                raise AgentError(str(exc)) from exc
        elif "files" in response:
            for path, content in response["files"].items():
                written.append(ctx.write(path, content))
        else:
            raise AgentError("provider returned neither files nor a patch")
        if not written:
            raise AgentError("change set is empty")
        return AgentResult(
            outputs={f"change:{task['id']}": {"task": task["id"], "files": sorted(written),
                                "source": response.get("source"), "attempt": ctx.attempt}},
            decisions=[Decision(
                id=f"impl:{task['id']}", node=ctx.node.id,
                summary=f"{task['title']} ({len(written)} files)",
                rationale=task.get("description", ""),
                based_on=["plan:decomposition", "spec"])],
            artifacts=sorted(written),
            summary=f"{task['id']}: {len(written)} files",
        )

"""Stateful, governed execution engine.

Control flow (one scheduling loop, many worker threads):

    schedule ready nodes  --(parallel, scope-disjoint)-->  worker: execute node
          ^                                                   | entry gates
          |                                                   | attempt 1..N: agent -> policy -> exit gates
          |                                                   |   failure: scoped rollback + feedback + retry
          |                                                   | fallback agent (optional)
          |                                                   | approval checkpoint (policy-driven)
          |                                                   | commit checkpoint
          +---- main thread: publish outputs, detect upstream changes (re-plan),
                apply graph mutations, persist state, honour safe-stop

All shared state (context, node states, graph) is mutated only on the main
thread; workers communicate through return values. That keeps parallel
execution deterministic and makes every state transition persistable.
"""

from __future__ import annotations

import fnmatch
import json
import signal
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import metrics as metrics_mod
from .agents import REGISTRY, AgentContext
from .approvals import APPROVE, REJECT, REQUEST_CHANGES, ApprovalDecision, ApprovalRequest, Approver
from .audit import AuditLog
from .context import ContextStore
from .gates import check_entry, run_exit_gates
from .graph import Graph, scopes_overlap
from .models import (
    AgentResult,
    ApprovalRejected,
    ChangesRequested,
    GateFailure,
    NodeSpec,
    NodeState,
    NodeStatus,
    PolicyViolation,
    RunStatus,
    stable_hash,
)
from .policy import FileChange, Policy
from .providers import Provider
from .workspace import Workspace, WorkspaceError


class NodeFailed(Exception):
    pass


@dataclass
class Outcome:
    result: AgentResult
    fingerprint: str
    changes: list[FileChange] = field(default_factory=list)
    checkpoint: str | None = None
    used_fallback: bool = False


class Engine:
    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        scenario: dict[str, Any],
        graph: Graph,
        workspace: Workspace,
        policy: Policy,
        provider: Provider,
        approver: Approver,
        context: ContextStore | None = None,
        states: dict[str, NodeState] | None = None,
        faults: set[str] | None = None,
        release_dir: Path | None = None,
    ) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.scenario = scenario
        self.graph = graph
        self.workspace = workspace
        self.policy = policy
        self.provider = provider
        self.approver = approver
        self.context = context or ContextStore()
        self.states = states or {nid: NodeState() for nid in graph.nodes}
        self.faults = set(faults or ())
        self.release_dir = release_dir or run_dir / "release"
        self.status = RunStatus.RUNNING
        self.audit = AuditLog(run_dir / "audit.jsonl", run_id)
        self.artifacts_dir = run_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.max_parallel = int(policy.config["autonomy"]["max_parallel_nodes"])
        self.approvals: list[dict[str, Any]] = []
        self.approval_counts: dict[str, int] = {}
        self.removed: dict[str, dict[str, Any]] = {}
        self._approval_lock = threading.Lock()
        self._halt: tuple[str, str] | None = None     # (mode, reason); mode = safe_stop | failed
        self._interrupted = False
        self._injected: set[str] = set()

    # =============================================================================
    # public API
    # =============================================================================
    def run(self, resumed: bool = False) -> RunStatus:
        self._install_interrupt_handler()
        self.audit.record("run.resumed" if resumed else "run.started", scenario=self.scenario["name"],
                          provider=self.provider.name, faults=sorted(self.faults),
                          nodes=sorted(self.graph.nodes))
        running: dict[Future, str] = {}
        with ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="agent") as pool:
            while True:
                if self._halt is None and self._stop_requested():
                    self._begin_halt("safe_stop", "operator requested safe-stop")
                if self._halt is None:
                    self._schedule(pool, running)
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    self._complete(running.pop(future), future)
                self.save_state()
        self._finish()
        return self.status

    # =============================================================================
    # scheduling (main thread)
    # =============================================================================
    def _schedule(self, pool: ThreadPoolExecutor, running: dict[Future, str]) -> None:
        changed = True
        while changed:
            changed = False
            running_ids = set(running.values())
            for nid in self.graph.topological_order():
                spec, st = self.graph.nodes[nid], self.states[nid]
                if st.status not in (NodeStatus.PENDING, NodeStatus.STALE) or nid in running_ids:
                    continue
                dep_states = [self.states[d].status for d in spec.depends_on]
                if any(s in (NodeStatus.FAILED, NodeStatus.BLOCKED) for s in dep_states):
                    st.status = NodeStatus.BLOCKED
                    changed = True
                    continue
                if not all(s == NodeStatus.SUCCEEDED for s in dep_states):
                    continue
                fingerprint = self._fingerprint(spec)
                if st.status == NodeStatus.STALE and self._cache_valid(st, fingerprint):
                    st.status = NodeStatus.SUCCEEDED
                    self.audit.record("node.cached", node=nid, fingerprint=fingerprint,
                                      detail="inputs unchanged since last successful run")
                    changed = True
                    continue
                if len(running) >= self.max_parallel:
                    return
                if spec.write_scope and any(
                    scopes_overlap(spec.write_scope, self.graph.nodes[r].write_scope) for r in running_ids
                ):
                    continue  # resource lock: never run two writers on the same paths
                if spec.rerun_strategy == "rewind" and st.checkpoint and self._checkpoint_valid(st.checkpoint):
                    if running:
                        continue  # rewinding needs a quiescent workspace
                    self._rewind_before(st.checkpoint, reason=f"re-running {nid} on its original base")
                st.status = NodeStatus.RUNNING
                st.error = None
                future = pool.submit(self._execute, nid, fingerprint)
                running[future] = nid
                running_ids.add(nid)
                changed = True

    def _fingerprint(self, spec: NodeSpec) -> str:
        exclude = set(spec.params.get("fingerprint_exclude", []))
        deps = {d: self.states[d].output_hash for d in spec.depends_on if d not in exclude}
        inputs = {k: (self.context.entry(k).hash if self.context.entry(k) else None)
                  for k in self._input_keys(spec) if k not in exclude}
        return stable_hash({"agent": spec.agent, "params": spec.params, "deps": deps, "inputs": inputs,
                            "scope": spec.write_scope})

    def _input_keys(self, spec: NodeSpec) -> list[str]:
        keys: list[str] = []
        for pattern in spec.params.get("inputs", []):
            if any(ch in pattern for ch in "*?["):
                keys += sorted(k for k in self.context.snapshot() if fnmatch.fnmatch(k, pattern))
            else:
                keys.append(pattern)
        return keys

    def _cache_valid(self, st: NodeState, fingerprint: str) -> bool:
        return (st.output_hash is not None and st.input_fingerprint == fingerprint
                and (st.checkpoint is None or self._checkpoint_valid(st.checkpoint)))

    def _checkpoint_valid(self, commit: str) -> bool:
        try:
            self.workspace.git("merge-base", "--is-ancestor", commit, "HEAD")
            return True
        except WorkspaceError:
            return False

    def _rewind_before(self, commit: str, reason: str) -> None:
        parent = self.workspace.git("rev-parse", f"{commit}^").strip()
        self.workspace.reset_to(parent)
        invalidated = []
        for nid, st in self.states.items():
            if st.checkpoint and not self._checkpoint_valid(st.checkpoint):
                st.checkpoint = None
                st.output_hash = None  # its changes are gone: must genuinely re-run
                if st.status == NodeStatus.SUCCEEDED:
                    st.status = NodeStatus.STALE
                invalidated.append(nid)
        self.audit.record("rollback.performed", scope="workspace", to=parent, reason=reason,
                          invalidated=sorted(invalidated))

    # =============================================================================
    # node execution (worker threads)
    # =============================================================================
    def _make_ctx(self, spec: NodeSpec, attempt: int, feedback: list[str]) -> AgentContext:
        return AgentContext(
            node=spec,
            inputs=self.context.snapshot(self._input_keys(spec)),
            workspace=self.workspace,
            provider=self.provider,
            policy=self.policy,
            attempt=attempt,
            feedback=list(feedback),
            artifacts_dir=self.artifacts_dir,
            scenario=self.scenario,
            audit=lambda event, **data: self.audit.record(event, **data),
            ask_human=self.request_approval,
            extra={"release_dir": str(self.release_dir), "faults": sorted(self.faults)},
        )

    def _execute(self, nid: str, fingerprint: str) -> Outcome:
        spec, st = self.graph.nodes[nid], self.states[nid]
        st.runs += 1
        st.started_at = time.time()
        self.audit.record("node.started", node=nid, stage=spec.stage, run=st.runs, fingerprint=fingerprint)

        try:
            check_entry(self._make_ctx(spec, 1, []), ["inputs_present"] + spec.entry_gates,
                        set(self.context.snapshot()))
        except GateFailure as exc:
            self.audit.record("gate.entry_failed", node=nid, gate=exc.gate, reason=exc.reason)
            raise NodeFailed(str(exc)) from exc
        self.audit.record("gate.entry_passed", node=nid, gates=["inputs_present"] + spec.entry_gates)

        agents = [(spec.agent, spec.retry.max_attempts, False)]
        if spec.fallback_agent:
            agents.append((spec.fallback_agent, 1, True))
        feedback: list[str] = []
        last_error = "no attempt made"
        for agent_name, attempts, is_fallback in agents:
            if is_fallback:
                self.audit.record("fallback.used", node=nid, agent=agent_name, after=last_error[:500])
            for attempt in range(1, attempts + 1):
                st.attempts += 1
                self.audit.record("attempt.started", node=nid, attempt=attempt, agent=agent_name)
                try:
                    result, changes = self._attempt(spec, agent_name, attempt, feedback)
                except (PolicyViolation, ApprovalRejected, ChangesRequested):
                    self._discard(spec, "guardrail stop")
                    raise
                except Exception as exc:  # noqa: BLE001 - any agent failure is retryable
                    last_error = f"{type(exc).__name__}: {exc}"
                    self.audit.record("attempt.failed", node=nid, attempt=attempt, agent=agent_name,
                                      error=last_error[:2000],
                                      gate=getattr(exc, "gate", None))
                    self._discard(spec, f"attempt {attempt} failed")
                    feedback.append(last_error[:2000])
                    if attempt < attempts:
                        self.audit.record("retry.scheduled", node=nid, next_attempt=attempt + 1)
                        time.sleep(spec.retry.backoff_seconds * attempt)
                    continue
                return self._approve_and_commit(spec, result, changes, fingerprint, is_fallback)
        raise NodeFailed(f"{nid} failed after retries: {last_error}")

    def _attempt(self, spec: NodeSpec, agent_name: str, attempt: int,
                 feedback: list[str]) -> tuple[AgentResult, list[FileChange]]:
        ctx = self._make_ctx(spec, attempt, feedback)
        result = REGISTRY[agent_name].run(ctx)
        self._inject_faults(spec)
        changes = self.workspace.pending_changes(spec.write_scope) if spec.write_scope else []
        findings = self.policy.enforce_change_set(spec, changes)
        if findings:
            self.audit.record("policy.findings", node=spec.id, findings=[f.to_dict() for f in findings])
        passed = run_exit_gates(ctx, result, spec.exit_gates)
        self.audit.record("gate.exit_passed", node=spec.id, gates=passed, attempt=attempt)
        return result, changes

    def _inject_faults(self, spec: NodeSpec) -> None:
        """Deliberate faults used to demonstrate guardrails (never active by default)."""
        if "security_violation" in self.faults and spec.stage == "implement" and "sec" not in self._injected:
            for ch in self.workspace.pending_changes(spec.write_scope):
                if ch.path.endswith(".py") and ch.kind != "deleted":
                    self._injected.add("sec")
                    with (self.workspace.root / ch.path).open("a") as fh:
                        fh.write('\nADMIN_API_TOKEN = "sk_live_51HqLyjWDarjtT1zdp7dcXd"\n')
                    self.audit.record("fault.injected", node=spec.id, fault="security_violation", path=ch.path)
                    return

    def _discard(self, spec: NodeSpec, reason: str) -> None:
        if not spec.write_scope:
            return
        discarded = self.workspace.discard(spec.write_scope)
        if discarded:
            self.audit.record("rollback.performed", node=spec.id, scope=spec.write_scope,
                              files=discarded, reason=reason)

    def _approve_and_commit(self, spec: NodeSpec, result: AgentResult, changes: list[FileChange],
                            fingerprint: str, used_fallback: bool) -> Outcome:
        actions = self.policy.classify(changes)
        reasons = self.policy.approval_reasons(spec, actions)
        if reasons:
            checkpoint = spec.approval or "change_review"
            summary = result.summary
            if changes:
                diff = self.workspace.git("diff", "HEAD", "--stat", "--", *[c.path for c in changes if c.kind != "added"]) \
                    if any(c.kind != "added" for c in changes) else ""
                added = [c.path for c in changes if c.kind == "added"]
                summary += f"\n\nfiles: {[c.path for c in changes]}\n{diff}" + (f"new files: {added}" if added else "")
                summary += f"\ndetected actions: {sorted(actions) or 'none'}"
            decision = self.request_approval(ApprovalRequest(
                checkpoint=checkpoint, node=spec.id, title=f"{spec.stage}: {spec.id}", reasons=reasons,
                summary=summary, risk=spec.risk.value))
            if decision.decision == REJECT:
                raise ApprovalRejected(checkpoint, decision.approver, decision.comment)
            if decision.decision == REQUEST_CHANGES:
                amendment = dict(decision.amendment)
                amendment.setdefault("requirement", decision.comment)
                amendment["approver"] = decision.approver
                raise ChangesRequested(checkpoint, decision.approver, decision.comment, amendment)
            if decision.decision != APPROVE:
                raise ApprovalRejected(checkpoint, decision.approver, f"unexpected decision {decision.decision}")
        commit = None
        if spec.write_scope:
            commit = self.workspace.checkpoint(f"[{spec.id}] {result.summary.splitlines()[0][:72]}",
                                               spec.write_scope)
            self.audit.record("checkpoint.created", node=spec.id, commit=commit,
                              files=[c.path for c in changes])
        return Outcome(result, fingerprint, changes, commit, used_fallback)

    def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Route a checkpoint to the human approver (callable from any thread)."""
        with self._approval_lock:
            self.approval_counts[request.checkpoint] = self.approval_counts.get(request.checkpoint, 0) + 1
            request.occurrence = self.approval_counts[request.checkpoint]
        self.audit.record("approval.requested", node=request.node, checkpoint=request.checkpoint,
                          occurrence=request.occurrence, reasons=request.reasons, risk=request.risk,
                          summary=request.summary[:4000], questions=[q["id"] for q in request.questions])
        started = time.time()
        decision = self.approver.decide(request)
        record = {"checkpoint": request.checkpoint, "node": request.node, "occurrence": request.occurrence,
                  "decision": decision.decision, "approver": decision.approver, "comment": decision.comment,
                  "answers": decision.answers}
        with self._approval_lock:
            self.approvals.append(record)
        self.audit.record("approval.decided", node=request.node, actor=f"human:{decision.approver}",
                          wait_s=round(time.time() - started, 3), **{k: v for k, v in record.items() if k != "node"})
        return decision

    # =============================================================================
    # completion handling (main thread)
    # =============================================================================
    def _complete(self, nid: str, future: Future) -> None:
        spec, st = self.graph.nodes[nid], self.states[nid]
        st.ended_at = time.time()
        duration = round(st.ended_at - (st.started_at or st.ended_at), 3)
        self.context.publish("approvals", list(self.approvals), produced_by="engine", run=len(self.approvals))
        try:
            outcome: Outcome = future.result()
        except ChangesRequested as exc:
            st.status = NodeStatus.STALE
            self.audit.record("node.changes_requested", node=nid, checkpoint=exc.checkpoint,
                              approver=exc.approver, comment=exc.comment)
            self._replan(exc.amendment, source=nid)
            return
        except PolicyViolation as exc:
            self._fail(nid, str(exc), duration)
            self.audit.record("policy.violation", node=nid, rule=exc.rule, detail=exc.detail)
            self._begin_halt("safe_stop", f"policy violation in {nid}: {exc.rule}")
            return
        except ApprovalRejected as exc:
            self._fail(nid, str(exc), duration)
            self._begin_halt("safe_stop", f"approval '{exc.checkpoint}' rejected by {exc.approver}")
            return
        except Exception as exc:  # noqa: BLE001 - NodeFailed or engine bug: fail safe either way
            self._fail(nid, f"{type(exc).__name__}: {exc}", duration)
            self._begin_halt("failed", f"{nid} failed: {exc}")
            return

        result = outcome.result
        mutation = result.outputs.pop("_graph_mutation", None)
        previous_hash = st.output_hash
        st.output = result.outputs
        st.output_hash = stable_hash(result.outputs)
        st.input_fingerprint = outcome.fingerprint
        st.checkpoint = outcome.checkpoint
        st.used_fallback = outcome.used_fallback
        st.status = NodeStatus.SUCCEEDED
        inputs = self._input_keys(spec)
        for key, value in result.outputs.items():
            self.context.publish(key, value, produced_by=nid, run=st.runs, inputs=inputs)
        for decision in result.decisions:
            self.context.record_decision(decision)
        self.audit.record("node.succeeded", node=nid, stage=spec.stage, duration_s=duration,
                          attempts=st.attempts, output_hash=st.output_hash, summary=result.summary[:500],
                          artifacts=result.artifacts)
        if mutation:
            self._apply_mutation(mutation, source=nid)
        if previous_hash and previous_hash != st.output_hash:
            stale = [d for d in sorted(self.graph.dependents_map()[nid])
                     if self.states[d].status == NodeStatus.SUCCEEDED]
            for d in stale:
                self.states[d].status = NodeStatus.STALE
            if stale:
                self.audit.record("replan.triggered", node=nid, reason="upstream output changed",
                                  previous_hash=previous_hash, new_hash=st.output_hash, invalidated=stale)

    def _fail(self, nid: str, error: str, duration: float) -> None:
        st = self.states[nid]
        st.status = NodeStatus.FAILED
        st.error = error
        self.audit.record("node.failed", node=nid, stage=self.graph.nodes[nid].stage,
                          duration_s=duration, error=error[:2000])
        for d in self.graph.descendants(nid):
            if not self.states[d].status.terminal:
                self.states[d].status = NodeStatus.BLOCKED

    def _replan(self, amendment: dict[str, Any], source: str) -> None:
        target = amendment.get("target", "requirements")
        amendments = list(self.context.get("amendments", [])) + [amendment]
        self.context.publish("amendments", amendments, produced_by=f"human:{amendment.get('approver')}",
                             run=len(amendments))
        invalidated = [target] + sorted(self.graph.descendants(target))
        for nid in invalidated:
            if self.states[nid].status in (NodeStatus.SUCCEEDED, NodeStatus.STALE, NodeStatus.PENDING):
                self.states[nid].status = NodeStatus.STALE
        self.audit.record("replan.triggered", node=source, reason="changes requested at checkpoint",
                          amendment=amendment, target=target, invalidated=invalidated)

    def _apply_mutation(self, mutation: dict[str, Any], source: str) -> None:
        proposed = {d["id"]: NodeSpec.from_dict(d) for d in mutation["nodes"]}
        added, updated, removed = [], [], []
        for nid, spec in proposed.items():
            existing = self.graph.nodes.get(nid)
            if existing is None:
                self.graph.add(spec, validate=False)
                self.states[nid] = NodeState()
                added.append(nid)
            elif existing.to_dict() != spec.to_dict():
                self.graph.nodes[nid] = spec
                if self.states[nid].status == NodeStatus.SUCCEEDED:
                    self.states[nid].status = NodeStatus.STALE
                updated.append(nid)
        for nid in [n for n, s in self.graph.nodes.items() if s.dynamic and n not in proposed]:
            st = self.states[nid]
            if st.checkpoint and self._checkpoint_valid(st.checkpoint):
                self._rewind_before(st.checkpoint, reason=f"task {nid} removed by re-plan")
            self.removed[nid] = {"spec": self.graph.nodes[nid].to_dict(), "state": st.to_dict()}
            self.graph.remove(nid)
            del self.states[nid]
            removed.append(nid)
        dynamic_ids = sorted(n for n, s in self.graph.nodes.items() if s.dynamic)
        for join in mutation.get("join", []):
            if join in self.graph.nodes:
                node = self.graph.nodes[join]
                static = [d for d in node.depends_on if not self.graph.nodes[d].dynamic]
                node.depends_on = static + dynamic_ids
                if self.states[join].status == NodeStatus.SUCCEEDED and (added or removed):
                    self.states[join].status = NodeStatus.STALE
        self.graph.version += 1
        self.graph.validate()
        self.audit.record("graph.mutated", node=source, added=added, updated=updated, removed=removed,
                          graph_version=self.graph.version, waves=self.graph.levels())

    # =============================================================================
    # safe-stop and completion
    # =============================================================================
    def _begin_halt(self, mode: str, reason: str) -> None:
        if self._halt is None:
            self._halt = (mode, reason)
            self.audit.record("halt.requested", mode=mode, reason=reason)

    def _stop_requested(self) -> bool:
        return self._interrupted or (self.run_dir / "STOP").exists()

    def _install_interrupt_handler(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def handler(signum, frame):  # noqa: ARG001
            self._interrupted = True

        try:
            signal.signal(signal.SIGINT, handler)
        except ValueError:
            pass

    def _finish(self) -> None:
        if self._halt:
            mode, reason = self._halt
            # Discard anything uncommitted: the workspace returns to the last good checkpoint.
            self.workspace.reset_to(self.workspace.head())
            self.audit.record("safe_stop" if mode == "safe_stop" else "run.halted", reason=reason,
                              checkpoint=self.workspace.head())
            self.status = RunStatus.SAFE_STOPPED if mode == "safe_stop" else RunStatus.FAILED
        elif all(st.status == NodeStatus.SUCCEEDED for st in self.states.values()):
            self.status = RunStatus.SUCCEEDED
        else:
            self.status = RunStatus.FAILED
        self.audit.record("run.finished", status=self.status.value,
                          node_status={n: s.status.value for n, s in self.states.items()})
        self.save_state()
        run_metrics = metrics_mod.compute(self.audit.events())
        (self.run_dir / "metrics.json").write_text(json.dumps(run_metrics, indent=2))
        metrics_mod.append_history(self.run_dir.parent / "metrics_history.jsonl", self.run_id,
                                   self.scenario["name"], run_metrics)
        from .report import write_report

        write_report(self)

    # =============================================================================
    # persistence
    # =============================================================================
    def save_state(self) -> None:
        state = {
            "run_id": self.run_id,
            "status": self.status.value,
            "scenario": self.scenario,
            "faults": sorted(self.faults),
            "halt": self._halt,
            "graph_version": self.graph.version,
            "nodes": [self.graph.nodes[n].to_dict() for n in self.graph.topological_order()],
            "states": {n: s.to_dict() for n, s in self.states.items()},
            "removed": self.removed,
            "approvals": self.approvals,
            "approval_counts": self.approval_counts,
            "context": self.context.to_dict(),
        }
        tmp = self.run_dir / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.replace(self.run_dir / "state.json")  # atomic: a crash never leaves a torn state file

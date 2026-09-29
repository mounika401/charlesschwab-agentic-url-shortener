"""Engine behaviour on small synthetic graphs with fake agents."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from orchestrator.agents import REGISTRY, AgentError
from orchestrator.approvals import APPROVE, REJECT, REQUEST_CHANGES, ApprovalDecision
from orchestrator.context import ContextStore
from orchestrator.engine import Engine
from orchestrator.graph import Graph
from orchestrator.lifecycle import POLICY_PATH
from orchestrator.models import AgentResult, NodeSpec, NodeStatus, RetryPolicy, RunStatus
from orchestrator.policy import Policy
from orchestrator.workspace import Workspace


class Writer:
    """Writes one file; can fail the first N attempts or sleep to expose concurrency."""

    def __init__(self, name, path, content="x = 1\n", fail_times=0, sleep=0.0, log=None):
        self.name, self.path, self.content = name, path, content
        self.fail_times, self.sleep, self.log = fail_times, sleep, log
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if self.log is not None:
            self.log.append(("start", self.name, time.time()))
        ctx.write(self.path, self.content + f"# attempt {ctx.attempt}\n")
        time.sleep(self.sleep)
        if self.log is not None:
            self.log.append(("end", self.name, time.time()))
        if self.calls <= self.fail_times:
            raise AgentError(f"{self.name} transient failure")
        return AgentResult(outputs={f"out:{self.name}": ctx.inputs.get("spec", "none")}, summary=self.name)


class Echo:
    def __init__(self, name):
        self.name = name
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        spec = {"amendments": len(ctx.inputs.get("amendments", []))}
        return AgentResult(outputs={"spec": spec}, summary="echo")


class Approver:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return self.decisions.pop(0) if self.decisions else ApprovalDecision(APPROVE, "tester")


class NullProvider:
    name = "none"

    def generate(self, kind, request):
        raise AssertionError("not used")


def make_engine(tmp_path: Path, nodes, approver=None, context=None) -> Engine:
    ws = Workspace(tmp_path / "ws")
    ws.init()
    ctx = context or ContextStore()
    return Engine(run_id="t", run_dir=tmp_path, scenario={"name": "test", "type": "test"}, graph=Graph(nodes),
                  workspace=ws, policy=Policy.load(POLICY_PATH), provider=NullProvider(),
                  approver=approver or Approver(), context=ctx)


@pytest.fixture
def register(monkeypatch):
    def _register(*agents):
        for agent in agents:
            monkeypatch.setitem(REGISTRY, agent.name, agent)
    return _register


def events(engine, name):
    return [e for e in engine.audit.events() if e["event"] == name]


def test_disjoint_scopes_run_in_parallel_and_overlapping_scopes_do_not(tmp_path, register):
    log: list = []
    register(Writer("w1", "a/one.py", sleep=0.3, log=log), Writer("w2", "b/two.py", sleep=0.3, log=log),
             Writer("w3", "a/three.py", sleep=0.3, log=log))
    engine = make_engine(tmp_path, [
        NodeSpec("n1", "s", "w1", write_scope=["a/**"]),
        NodeSpec("n2", "s", "w2", write_scope=["b/**"]),
        NodeSpec("n3", "s", "w3", write_scope=["a/**"]),
    ])
    assert engine.run() == RunStatus.SUCCEEDED
    spans = {}
    for kind, name, ts in log:
        spans.setdefault(name, {})[kind] = ts
    overlap = lambda x, y: spans[x]["start"] < spans[y]["end"] and spans[y]["start"] < spans[x]["end"]
    assert overlap("w1", "w2") or overlap("w3", "w2")  # different scopes overlapped in time
    assert not overlap("w1", "w3")                      # same scope was serialised
    assert len(engine.workspace.log()) == 4             # baseline + one checkpoint per node


def test_retry_rolls_back_failed_attempt_then_succeeds(tmp_path, register):
    agent = Writer("flaky", "src/mod.py", fail_times=1)
    register(agent)
    engine = make_engine(tmp_path, [NodeSpec("n", "s", "flaky", write_scope=["src/**"],
                                             retry=RetryPolicy(max_attempts=3))])
    assert engine.run() == RunStatus.SUCCEEDED
    assert agent.calls == 2
    assert len(events(engine, "rollback.performed")) == 1
    assert len(events(engine, "retry.scheduled")) == 1
    assert "# attempt 2" in (engine.workspace.root / "src/mod.py").read_text()
    metrics = __import__("json").loads((tmp_path / "metrics.json").read_text())
    assert metrics["mttr_s"] is not None and metrics["incidents_recovered"] == 1


def test_exhausted_retries_fail_the_run_and_block_dependents(tmp_path, register):
    register(Writer("broken", "src/x.py", fail_times=99), Writer("after", "src/y.py"))
    engine = make_engine(tmp_path, [
        NodeSpec("n1", "s", "broken", write_scope=["src/x.py"], retry=RetryPolicy(max_attempts=2)),
        NodeSpec("n2", "s", "after", depends_on=["n1"], write_scope=["src/y.py"]),
    ])
    assert engine.run() == RunStatus.FAILED
    assert engine.states["n1"].status == NodeStatus.FAILED
    assert engine.states["n2"].status == NodeStatus.BLOCKED
    assert not (engine.workspace.root / "src/x.py").exists()  # failed attempt rolled back


def test_fallback_agent_runs_after_primary_exhausts_retries(tmp_path, register):
    register(Writer("primary", "src/x.py", fail_times=99), Writer("backup", "src/x.py", content="y = 2\n"))
    engine = make_engine(tmp_path, [NodeSpec("n", "s", "primary", write_scope=["src/**"],
                                             retry=RetryPolicy(max_attempts=2), fallback_agent="backup")])
    assert engine.run() == RunStatus.SUCCEEDED
    assert len(events(engine, "fallback.used")) == 1
    assert "y = 2" in (engine.workspace.root / "src/x.py").read_text()


def test_policy_violation_safe_stops_and_restores_last_checkpoint(tmp_path, register):
    register(Writer("ok", "src/a.py"), Writer("leaky", "src/b.py", content='API_TOKEN = "abcdefghijklmnop1234"\n'))
    engine = make_engine(tmp_path, [
        NodeSpec("n1", "s", "ok", write_scope=["src/a.py"]),
        NodeSpec("n2", "s", "leaky", depends_on=["n1"], write_scope=["src/b.py"]),
    ])
    assert engine.run() == RunStatus.SAFE_STOPPED
    assert len(events(engine, "policy.violation")) == 1
    assert (engine.workspace.root / "src/a.py").exists()        # committed work kept
    assert not (engine.workspace.root / "src/b.py").exists()    # violating change discarded
    assert engine.workspace.pending_changes() == []


def test_rejected_approval_safe_stops(tmp_path, register):
    register(Writer("w", "src/a.py"))
    approver = Approver(ApprovalDecision(REJECT, "reviewer", "no"))
    engine = make_engine(tmp_path, [NodeSpec("n", "s", "w", write_scope=["src/**"], approval="release")],
                         approver=approver)
    assert engine.run() == RunStatus.SAFE_STOPPED
    assert approver.requests[0].checkpoint == "release"
    assert not (engine.workspace.root / "src/a.py").exists()


def test_changes_requested_replans_and_reuses_unaffected_work(tmp_path, register):
    echo, independent = Echo("echo"), Writer("independent", "lib/x.py")
    register(echo, independent, Writer("gate", "gate.txt"))
    ctx = ContextStore()
    ctx.publish("amendments", [], produced_by="engine", run=0)
    approver = Approver(ApprovalDecision(REQUEST_CHANGES, "reviewer", "add X", amendment={"target": "req"}),
                        ApprovalDecision(APPROVE, "reviewer", "ok"))
    engine = make_engine(tmp_path, [
        NodeSpec("req", "requirements", "echo", params={"inputs": ["amendments"]}),
        NodeSpec("lib", "implement", "independent", write_scope=["lib/**"],
                 params={"fingerprint_exclude": []}),
        NodeSpec("release", "release", "gate", depends_on=["req", "lib"], write_scope=["gate.txt"],
                 approval="release"),
    ], approver=approver, context=ctx)
    assert engine.run() == RunStatus.SUCCEEDED
    assert echo.calls == 2                                  # re-ran with the amendment
    assert independent.calls == 1                           # unaffected branch not re-executed
    assert engine.context.get("spec") == {"amendments": 1}
    assert len(events(engine, "replan.triggered")) == 1
    assert [r.occurrence for r in approver.requests] == [1, 2]


def test_stop_file_safe_stops_and_resume_completes(tmp_path, register):
    register(Writer("slow", "a.py", sleep=0.2), Writer("next", "b.py"))
    nodes = [NodeSpec("n1", "s", "slow", write_scope=["a.py"]),
             NodeSpec("n2", "s", "next", depends_on=["n1"], write_scope=["b.py"])]
    engine = make_engine(tmp_path, nodes)
    threading.Timer(0.05, lambda: (tmp_path / "STOP").write_text("stop")).start()
    assert engine.run() == RunStatus.SAFE_STOPPED
    assert engine.states["n1"].status == NodeStatus.SUCCEEDED   # in-flight work finished cleanly
    assert engine.states["n2"].status == NodeStatus.PENDING     # nothing new started
    assert (tmp_path / "state.json").exists()

    (tmp_path / "STOP").unlink()
    resumed = Engine(run_id="t", run_dir=tmp_path, scenario=engine.scenario, graph=engine.graph,
                     workspace=engine.workspace, policy=engine.policy, provider=NullProvider(),
                     approver=Approver(), context=engine.context, states=engine.states)
    assert resumed.run(resumed=True) == RunStatus.SUCCEEDED
    assert (engine.workspace.root / "b.py").exists()


def test_entry_gate_rejects_missing_inputs_without_retrying(tmp_path, register):
    agent = Writer("w", "a.py")
    register(agent)
    engine = make_engine(tmp_path, [NodeSpec("n", "s", "w", params={"inputs": ["spec"]}, write_scope=["a.py"])])
    assert engine.run() == RunStatus.FAILED
    assert agent.calls == 0
    assert events(engine, "gate.entry_failed")

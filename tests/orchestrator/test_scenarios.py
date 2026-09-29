"""End-to-end: the three scenarios, fault injection, resume and reproducibility.

These run the real pipeline (real git workspaces, real pytest runs of the
generated service), so they take ~30-60s in total.
"""

from __future__ import annotations

import filecmp
import json
from pathlib import Path

import pytest

from orchestrator.audit import verify_chain
from orchestrator.lifecycle import REPO_ROOT, create_run, materialize, resume_run, scaffold
from orchestrator.models import NodeStatus, RunStatus
from orchestrator.workspace import Workspace


def events(engine, name):
    return [e for e in engine.audit.events() if e["event"] == name]


@pytest.fixture(scope="module")
def runs_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("runs")


@pytest.fixture(scope="module")
def completed(runs_dir):
    """Run the three scenarios once, chained like `python -m orchestrator demo`."""
    results = {}
    previous = None
    for name in ("greenfield", "brownfield", "ambiguous"):
        engine = create_run(name, runs_dir=runs_dir, baseline_from=previous)
        engine.run()
        results[name] = engine
        previous = engine.release_dir
    return results


@pytest.mark.parametrize("name", ["greenfield", "brownfield", "ambiguous"])
def test_scenario_succeeds_with_verified_audit_trail(completed, name):
    engine = completed[name]
    assert engine.status == RunStatus.SUCCEEDED, (engine.run_dir / "report.md").read_text()[-3000:]
    assert verify_chain(engine.run_dir / "audit.jsonl")[0]
    assert (engine.run_dir / "report.md").exists()
    verification = engine.context.get("verification")
    assert verification["failed"] == 0 and verification["passed"] >= engine.scenario["min_tests"]
    assert engine.context.get("verification")["contract"]["missing"] == []
    assert all(c["passed"] for c in engine.context.get("readiness")["checks"])


def test_greenfield_decomposes_into_parallel_waves_with_policy_checkpoints(completed):
    engine = completed["greenfield"]
    assert {n for n in engine.graph.nodes if n.startswith("impl:")} == {f"impl:G{i}" for i in range(1, 7)}
    waves = engine.context.get("plan")["waves"]
    assert ["G1", "G6"] in waves and ["G2", "G3"] in waves
    checkpoints = {(a["checkpoint"], a["node"]) for a in engine.approvals}
    assert ("change_review", "impl:G3") in checkpoints      # schema migration
    assert ("change_review", "impl:G6") in checkpoints      # new dependency
    assert ("release", "release_approval") in checkpoints


def test_brownfield_analyses_impact_and_recovers_from_incomplete_fix(completed):
    engine = completed["brownfield"]
    codebase = engine.context.get("codebase")
    assert "shortener.link_service" in codebase["impacted"]
    assert "shortener.app" in codebase["impacted"] or "shortener.app" in codebase["blast_radius"]
    assert engine.states["impl:B4"].attempts == 2
    failed = events(engine, "attempt.failed")
    assert failed and failed[0]["node"] == "impl:B4" and "test_bug101" in failed[0]["data"]["error"]
    assert any(e["node"] == "impl:B4" for e in events(engine, "rollback.performed"))
    assert json.loads((engine.run_dir / "metrics.json").read_text())["mttr_s"] is not None


def test_ambiguous_clarifies_then_replans_incrementally_on_change_request(completed):
    engine = completed["ambiguous"]
    spec = engine.context.get("spec")
    assert {"safe", "robust", "public use"} <= set(spec["ambiguity"]["vague_terms"])
    assert "redirect_rate_limit" in spec["flags"]
    replans = events(engine, "replan.triggered")
    assert replans and replans[0]["data"]["reason"] == "changes requested at checkpoint"
    assert {e["node"] for e in events(engine, "node.cached")} >= {f"impl:A{i}" for i in range(1, 6)}
    added = [e["data"]["added"] for e in events(engine, "graph.mutated")]
    assert added[-1] == ["impl:A6"]
    releases = [a for a in engine.approvals if a["checkpoint"] == "release"]
    assert [a["decision"] for a in releases] == ["request_changes", "approve"]
    assert "T5" not in engine.context.get("threats")["residual"]   # redirect flooding now mitigated


def test_chained_release_equals_committed_service(completed):
    release = completed["ambiguous"].release_dir
    for folder in ("shortener", "tests/shortener"):
        cmp = filecmp.dircmp(release / folder, REPO_ROOT / folder, ignore=["__pycache__"])
        assert not (cmp.left_only or cmp.right_only or cmp.diff_files), (folder, cmp.left_only, cmp.right_only,
                                                                           cmp.diff_files)


def test_materialised_baseline_equals_committed_service(tmp_path):
    ws = Workspace(tmp_path / "ws")
    scaffold(ws)
    materialize(ws, ["greenfield", "brownfield", "ambiguous"])
    for folder in ("shortener", "tests/shortener"):
        cmp = filecmp.dircmp(ws.root / folder, REPO_ROOT / folder, ignore=["__pycache__"])
        assert not (cmp.left_only or cmp.right_only or cmp.diff_files)


def test_provider_outage_falls_back(runs_dir):
    engine = create_run("greenfield", runs_dir=runs_dir, faults={"provider_outage"})
    assert engine.run() == RunStatus.SUCCEEDED
    assert {e["data"]["kind"] for e in events(engine, "fallback.used")} == {"design", "plan"}


def test_security_violation_safe_stops_and_resume_completes(runs_dir):
    engine = create_run("greenfield", runs_dir=runs_dir, faults={"security_violation"})
    assert engine.run() == RunStatus.SAFE_STOPPED
    violation = events(engine, "policy.violation")[0]
    assert violation["data"]["rule"].startswith("security.secret")
    assert engine.workspace.pending_changes() == []
    assert "sk_live" not in "".join(p.read_text() for p in engine.workspace.root.rglob("*.py"))

    resumed = resume_run(engine.run_id, runs_dir=runs_dir)
    assert resumed.run(resumed=True) == RunStatus.SUCCEEDED
    assert verify_chain(engine.run_dir / "audit.jsonl")[0]      # one chain across both sessions


def test_failed_smoke_test_rolls_back_promotion(runs_dir):
    engine = create_run("greenfield", runs_dir=runs_dir, faults={"smoke_failure"})
    assert engine.run() == RunStatus.FAILED
    assert engine.states["release"].status == NodeStatus.FAILED
    assert not engine.release_dir.exists()
    assert any(e["data"].get("scope") == "release" for e in events(engine, "rollback.performed"))

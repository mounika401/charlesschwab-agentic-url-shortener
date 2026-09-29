import json

import httpx
import pytest

from orchestrator.audit import AuditLog, verify_chain
from orchestrator.context import ContextStore
from orchestrator.metrics import compute
from orchestrator.models import Decision
from orchestrator.providers import (
    AnthropicProvider,
    FallbackProvider,
    FaultInjectingProvider,
    ProviderError,
    render_conditionals,
)
from orchestrator.agents.requirements import detect_vague_terms


# ---- audit ----------------------------------------------------------------------------
def test_audit_chain_verifies_and_detects_tampering(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, "run-1")
    for i in range(5):
        log.record("event", node=f"n{i}", value=i)
    assert verify_chain(path) == (True, "5 records verified")

    lines = path.read_text().splitlines()
    record = json.loads(lines[2])
    record["data"]["value"] = 999
    lines[2] = json.dumps(record, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    ok, message = verify_chain(path)
    assert not ok and "line 3" in message


def test_audit_detects_deleted_records(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, "run-1")
    for i in range(3):
        log.record("event", value=i)
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert not verify_chain(path)[0]


def test_audit_resumes_chain_after_restart(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path, "r").record("a")
    AuditLog(path, "r").record("b")  # new process, same file
    assert verify_chain(path)[0]


# ---- context / lineage ----------------------------------------------------------------
def test_context_lineage_traces_values_and_decisions():
    ctx = ContextStore()
    ctx.publish("requirement", "text", produced_by="human:po", run=1)
    ctx.publish("spec", {"goal": "g"}, produced_by="requirements", run=1, inputs=["requirement"])
    ctx.record_decision(Decision("ADR-1", "design", "use sqlite", based_on=["spec"]))
    lineage = "\n".join(ctx.lineage("ADR-1"))
    assert "ADR-1" in lineage and "spec (from requirements" in lineage and "requirement (from human:po" in lineage


def test_context_snapshot_is_isolated():
    ctx = ContextStore()
    ctx.publish("spec", {"flags": []}, produced_by="x", run=1)
    snap = ctx.snapshot(["spec"])
    snap["spec"]["flags"].append("mutated")
    assert ctx.get("spec") == {"flags": []}


def test_context_round_trip():
    ctx = ContextStore()
    ctx.publish("k", [1, 2], produced_by="n", run=2)
    ctx.record_decision(Decision("d1", "n", "s"))
    restored = ContextStore.from_dict(json.loads(json.dumps(ctx.to_dict())))
    assert restored.get("k") == [1, 2] and "d1" in restored.decisions


# ---- metrics --------------------------------------------------------------------------
def test_metrics_mttr_and_rates():
    ev = lambda event, ts, node=None, **data: {"event": event, "ts": ts, "node": node, "data": data}
    events = [
        ev("run.started", 0.0),
        ev("attempt.started", 1.0, "a"), ev("attempt.failed", 2.0, "a"), ev("retry.scheduled", 2.0, "a"),
        ev("rollback.performed", 2.0, "a"),
        ev("attempt.started", 3.0, "a"), ev("node.succeeded", 5.0, "a", stage="impl", duration_s=4.0),
        ev("attempt.started", 5.0, "b"), ev("node.succeeded", 6.0, "b", stage="verify", duration_s=1.0),
        ev("run.finished", 7.0, status="succeeded"),
    ]
    m = compute(events)
    assert m["mttr_s"] == 3.0
    assert m["attempt_success_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert m["retries"] == 1 and m["rollbacks"] == 1
    assert m["end_to_end_latency_s"] == 7.0
    assert m["node_success_rate"] == 1.0


# ---- providers ------------------------------------------------------------------------
class Stub:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    def generate(self, kind, request):
        self.calls += 1
        if self.fail:
            raise ProviderError("down")
        return {"ok": self.name}


def test_fallback_chain_records_and_recovers():
    seen = []
    chain = FallbackProvider([Stub("llm", fail=True), Stub("replay")], on_fallback=lambda *a: seen.append(a))
    assert chain.generate("plan", {})["ok"] == "replay"
    assert seen[0][:3] == ("plan", "llm", "replay")


def test_fallback_chain_fails_when_all_fail():
    with pytest.raises(ProviderError, match="all providers failed"):
        FallbackProvider([Stub("a", True), Stub("b", True)]).generate("plan", {})


def test_fault_injection_only_affects_selected_kinds():
    inner = Stub("inner")
    faulty = FaultInjectingProvider(inner, {"design"})
    with pytest.raises(ProviderError):
        faulty.generate("design", {})
    assert faulty.generate("plan", {})["ok"] == "inner"


def test_conditional_rendering():
    text = "a\n<!-- if:x -->\nonly x\n<!-- endif -->\nb\n"
    assert render_conditionals(text, {"x"}) == "a\nonly x\nb\n"
    assert render_conditionals(text, set()) == "a\nb\n"


def _anthropic(handler):
    return AnthropicProvider(api_key="test", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_anthropic_provider_parses_json_response():
    def handler(request):
        assert request.headers["x-api-key"] == "test"
        return httpx.Response(200, json={"content": [{"type": "text", "text": 'Here:\n{"tasks": []}'}]})

    assert _anthropic(handler).generate("plan", {"spec": {}}) == {"tasks": []}


@pytest.mark.parametrize("response", [
    httpx.Response(529, json={}),
    httpx.Response(200, json={"content": [{"type": "text", "text": "no json here"}]}),
    httpx.Response(200, json={"content": [{"type": "text", "text": "{broken"}]}),
])
def test_anthropic_provider_errors_are_provider_errors(response):
    with pytest.raises(ProviderError):
        _anthropic(lambda request: response).generate("plan", {})


def test_anthropic_provider_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        AnthropicProvider()


# ---- requirements heuristics ----------------------------------------------------------
def test_vague_terms_detected_unless_quantified():
    assert detect_vague_terms("Make it safe for public use and more robust") == ["public use", "robust", "safe"]
    assert detect_vague_terms("Redirects must be fast: under 50 ms p99") == []


def test_codebase_identifier_tokens():
    from orchestrator.agents.codebase import identifier_tokens

    assert identifier_tokens("generate_code") == {"generate", "code"}
    assert identifier_tokens("TokenBucketLimiter") == {"token", "bucket", "limiter"}
    assert "rate" not in identifier_tokens("generate_code")

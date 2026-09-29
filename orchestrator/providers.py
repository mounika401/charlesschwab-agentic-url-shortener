"""Generation providers ("the model behind the agent") with fallback.

Agents own *process*: what to ask for, how to validate it, where it may land.
Providers own *content generation*. Keeping them separate means the governance
layer is identical whether content comes from an LLM or from a replay.

* ``ScriptedProvider`` replays reviewed responses stored under
  ``scenarios/<name>/provider``. It is the default so runs are deterministic,
  offline and reviewable - the property you want when evaluating orchestration.
* ``AnthropicProvider`` asks Claude for the same structured responses.
* ``FallbackProvider`` chains providers; a failure in one is audited and the
  next is tried (e.g. LLM outage -> last reviewed plan).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def generate(self, kind: str, request: dict[str, Any]) -> dict[str, Any]: ...


def active(item: dict[str, Any], flags: set[str]) -> bool:
    """Items may be conditional on spec flags: ``when: flag`` or ``when: [a, b]``."""
    when = item.get("when")
    if when is None:
        return True
    needed = {when} if isinstance(when, str) else set(when)
    return needed <= flags


def render_conditionals(text: str, flags: set[str]) -> str:
    """Keep ``<!-- if:flag -->...<!-- endif -->`` blocks only when the flag is set."""
    pattern = re.compile(r"<!-- if:(\w+) -->\n?(.*?)<!-- endif -->\n?", re.S)
    return pattern.sub(lambda m: m.group(2) if m.group(1) in flags else "", text)


class ScriptedProvider:
    name = "scripted"

    def __init__(self, root: Path) -> None:
        self.root = root

    def generate(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        flags = set(request.get("flags", []))
        if kind == "requirements":
            return yaml.safe_load((self.root / "requirements.yaml").read_text())
        if kind == "design":
            data = yaml.safe_load((self.root / "design.yaml").read_text())
            return {
                "document": render_conditionals((self.root / "design.md").read_text(), flags),
                "endpoints": [e for e in data.get("endpoints", []) if active(e, flags)],
                "data_model": [d for d in data.get("data_model", []) if active(d, flags)],
                "adrs": [a for a in data.get("adrs", []) if active(a, flags)],
            }
        if kind == "plan":
            data = yaml.safe_load((self.root / "plan.yaml").read_text())
            tasks = [t for t in data["tasks"] if active(t, flags)]
            return {"tasks": tasks, "rationale": data.get("rationale", "")}
        if kind == "implement":
            return self._changes(request["task"]["id"], int(request.get("attempt", 1)))
        raise ProviderError(f"scripted provider has no responses for '{kind}'")

    def _changes(self, task_id: str, attempt: int) -> dict[str, Any]:
        base = self.root / "changes" / task_id
        variant = base / f"attempt{attempt}"
        source = variant if variant.exists() else base / "final"
        if not source.exists():
            raise ProviderError(f"no scripted change set for task {task_id}")
        patch = source / "change.patch"
        if patch.exists():
            return {"patch": patch.read_text(), "source": str(source.relative_to(self.root))}
        files = {
            p.relative_to(source).as_posix(): p.read_text()
            for p in sorted(source.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        }
        return {"files": files, "source": str(source.relative_to(self.root))}


class AnthropicProvider:
    """Claude-backed provider. Requires ANTHROPIC_API_KEY; not used by default."""

    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    SCHEMAS = {
        "requirements": '{"goal": str, "functional": [str], "non_functional": [str], '
                        '"acceptance_criteria": [str], "clarifications": [{"id","question","default","options"}], '
                        '"keywords": [str], "out_of_scope": [str]}',
        "design": '{"document": markdown str, "endpoints": [{"method","path","status"}], '
                  '"data_model": [{"table","columns"}], "adrs": [{"id","title","decision","rationale","alternatives"}]}',
        "plan": '{"tasks": [{"id","title","depends_on":[ids],"write_scope":[globs],"risk":"low|medium|high",'
                '"targeted_tests":[paths],"description"}], "rationale": str}',
        "implement": '{"files": {"relative/path.py": "full file content"}}',
    }

    def __init__(self, api_key: str | None = None, model: str | None = None, client: Any = None,
                 timeout: float = 120.0) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self.model = model or os.environ.get("ORCH_MODEL", "claude-sonnet-5-5")
        self.timeout = timeout
        if client is None:
            import httpx

            client = httpx.Client(timeout=timeout)
        self.client = client

    def generate(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        if kind not in self.SCHEMAS:
            raise ProviderError(f"unsupported kind {kind}")
        prompt = (
            f"You are the {kind} agent in a governed software delivery pipeline for a URL shortener "
            f"(Python, FastAPI, SQLite, pytest). Respond with ONLY a JSON object matching: {self.SCHEMAS[kind]}.\n"
            f"Only write files inside the task's write_scope.\n\nInput:\n"
            f"{json.dumps(request, indent=2, default=str)[:60000]}"
        )
        try:
            response = self.client.post(
                self.URL,
                headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": self.model, "max_tokens": 8000,
                      "messages": [{"role": "user", "content": prompt}]},
            )
        except Exception as exc:  # network errors are provider failures, not crashes
            raise ProviderError(f"anthropic request failed: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"anthropic returned HTTP {response.status_code}")
        text = "".join(b.get("text", "") for b in response.json().get("content", []))
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ProviderError("anthropic response contained no JSON object")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"anthropic returned invalid JSON: {exc}") from exc


class FaultInjectingProvider:
    """Wraps a provider and fails the first call for selected kinds (resilience demos/tests)."""

    def __init__(self, inner: Provider, fail_kinds: set[str], name: str = "primary") -> None:
        self.inner = inner
        self.fail_kinds = set(fail_kinds)
        self.name = f"{name}(fault-injected)"

    def generate(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        if kind in self.fail_kinds:
            raise ProviderError(f"simulated outage of primary provider for '{kind}'")
        return self.inner.generate(kind, request)


class FallbackProvider:
    def __init__(self, chain: list[Provider], on_fallback: Callable[[str, str, str, str], None] | None = None):
        if not chain:
            raise ValueError("fallback chain must not be empty")
        self.chain = chain
        self.on_fallback = on_fallback
        self.name = " -> ".join(p.name for p in chain)

    def generate(self, kind: str, request: dict[str, Any]) -> dict[str, Any]:
        errors = []
        for index, provider in enumerate(self.chain):
            try:
                result = provider.generate(kind, request)
                result.setdefault("_provider", provider.name)
                return result
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")
                if index + 1 < len(self.chain) and self.on_fallback:
                    self.on_fallback(kind, provider.name, self.chain[index + 1].name, str(exc))
        raise ProviderError("all providers failed: " + "; ".join(errors))

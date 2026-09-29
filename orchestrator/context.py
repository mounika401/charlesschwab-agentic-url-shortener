"""Cross-stage context store with provenance and decision lineage.

Agents never mutate shared state directly: they return outputs, and the engine
publishes them here from the scheduler thread. Every value records which node
(and which run of that node) produced it, so any artefact can be traced back
through the decisions and inputs that led to it.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .models import Decision, stable_hash


@dataclass
class Entry:
    key: str
    value: Any
    produced_by: str
    run: int
    hash: str
    at: float = field(default_factory=time.time)
    inputs: list[str] = field(default_factory=list)   # keys this value was derived from


class ContextStore:
    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}
        self._history: list[Entry] = []
        self.decisions: dict[str, Decision] = {}

    # ---- values -------------------------------------------------------------------
    def publish(self, key: str, value: Any, produced_by: str, run: int, inputs: list[str] | None = None) -> Entry:
        entry = Entry(key, value, produced_by, run, stable_hash(value), inputs=list(inputs or []))
        self._entries[key] = entry
        self._history.append(entry)
        return entry

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._entries.get(key)
        return entry.value if entry else default

    def has(self, key: str) -> bool:
        return key in self._entries

    def entry(self, key: str) -> Entry | None:
        return self._entries.get(key)

    def snapshot(self, keys: list[str] | None = None) -> dict[str, Any]:
        """Read-only copy handed to agents (they cannot mutate shared state)."""
        import copy

        selected = self._entries if keys is None else {k: self._entries[k] for k in keys if k in self._entries}
        return {k: copy.deepcopy(e.value) for k, e in selected.items()}

    def versions(self, key: str) -> list[Entry]:
        return [e for e in self._history if e.key == key]

    # ---- decisions ----------------------------------------------------------------
    def record_decision(self, decision: Decision) -> None:
        self.decisions[decision.id] = decision

    def lineage(self, key_or_decision: str, depth: int = 10) -> list[str]:
        """Human-readable provenance chain for a context key or decision id."""
        lines: list[str] = []
        seen: set[str] = set()

        def walk(ref: str, level: int) -> None:
            if level > depth or ref in seen:
                return
            seen.add(ref)
            pad = "  " * level
            if ref in self.decisions:
                d = self.decisions[ref]
                lines.append(f"{pad}- decision {d.id} by {d.decided_by} @ {d.node}: {d.summary}")
                for parent in d.based_on:
                    walk(parent, level + 1)
            elif ref in self._entries:
                e = self._entries[ref]
                lines.append(f"{pad}- {ref} (from {e.produced_by} run {e.run}, hash {e.hash})")
                for parent in e.inputs:
                    walk(parent, level + 1)
            else:
                lines.append(f"{pad}- {ref} (external input)")

        walk(key_or_decision, 0)
        return lines

    # ---- persistence --------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": {k: asdict(e) for k, e in self._entries.items()},
            "history": [{"key": e.key, "produced_by": e.produced_by, "run": e.run, "hash": e.hash, "at": e.at}
                        for e in self._history],
            "decisions": {k: asdict(d) for k, d in self.decisions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextStore":
        store = cls()
        for key, raw in data.get("entries", {}).items():
            store._entries[key] = Entry(**raw)
        store._history = [Entry(key=h["key"], value=None, produced_by=h["produced_by"], run=h["run"],
                                hash=h["hash"], at=h["at"]) for h in data.get("history", [])]
        store.decisions = {k: Decision(**d) for k, d in data.get("decisions", {}).items()}
        return store

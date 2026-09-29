"""Tamper-evident audit trail.

Each event is one JSON line carrying the SHA-256 of the previous line, forming
a hash chain: editing or deleting any past record breaks verification of every
record after it. Writes are serialised with a lock because agents run on worker
threads.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self._lock = threading.Lock()
        self._seq, self._prev = self._tail()

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        last = None
        with self.path.open() as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)
        return (last["seq"], last["hash"]) if last else (0, GENESIS)

    def record(self, event: str, *, node: str | None = None, actor: str = "system", **data: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            body = {
                "seq": self._seq,
                "ts": round(time.time(), 6),
                "run_id": self.run_id,
                "event": event,
                "node": node,
                "actor": actor,
                "data": data,
                "prev_hash": self._prev,
            }
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
            body["hash"] = digest
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(body, sort_keys=True, default=str) + "\n")
            self._prev = digest
            return body

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open() as fh:
            return [json.loads(line) for line in fh if line.strip()]


def verify_chain(path: Path) -> tuple[bool, str]:
    prev = GENESIS
    expected_seq = 1
    with path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            claimed = record.pop("hash")
            if record["prev_hash"] != prev:
                return False, f"line {lineno}: prev_hash does not match previous record"
            if record["seq"] != expected_seq:
                return False, f"line {lineno}: sequence gap (expected {expected_seq}, got {record['seq']})"
            actual = hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
            if actual != claimed:
                return False, f"line {lineno}: record content was modified"
            prev = claimed
            expected_seq += 1
    return True, f"{expected_seq - 1} records verified"

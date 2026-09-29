"""In-process token-bucket rate limiter.

Each key (client identity) gets a bucket of ``capacity`` tokens refilled at
``capacity / period`` tokens per second, allowing short bursts while bounding
the sustained rate. State is per process: with N replicas the effective limit
is N x capacity. That is an accepted prototype trade-off; the interface is
kept small so a shared (e.g. Redis) implementation can replace it.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after_seconds: float = 0.0


class TokenBucketLimiter:
    def __init__(
        self,
        capacity: int,
        period_seconds: float = 60.0,
        max_keys: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0 or period_seconds <= 0:
            raise ValueError("capacity and period must be positive")
        self._capacity = float(capacity)
        self._rate = capacity / period_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = threading.Lock()

    def acquire(self, key: str) -> Decision:
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.pop(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate)
            if tokens >= 1.0:
                decision = Decision(True)
                tokens -= 1.0
            else:
                decision = Decision(False, (1.0 - tokens) / self._rate)
            self._buckets[key] = (tokens, now)  # re-insert as most recently used
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)  # bound memory: evict least recent
            return decision

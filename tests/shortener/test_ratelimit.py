import threading

import pytest

from shortener.ratelimit import TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_allows_burst_then_limits_and_reports_retry_after():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=3, period_seconds=60, clock=clock)
    assert all(limiter.acquire("a").allowed for _ in range(3))
    denied = limiter.acquire("a")
    assert not denied.allowed
    assert denied.retry_after_seconds == pytest.approx(20.0)


def test_refills_over_time():
    clock = FakeClock()
    limiter = TokenBucketLimiter(capacity=2, period_seconds=60, clock=clock)
    limiter.acquire("a"), limiter.acquire("a")
    assert not limiter.acquire("a").allowed
    clock.now += 30  # one token refilled
    assert limiter.acquire("a").allowed
    assert not limiter.acquire("a").allowed


def test_keys_are_isolated():
    limiter = TokenBucketLimiter(capacity=1, clock=FakeClock())
    assert limiter.acquire("a").allowed
    assert limiter.acquire("b").allowed
    assert not limiter.acquire("a").allowed


def test_memory_is_bounded_by_evicting_least_recent_key():
    limiter = TokenBucketLimiter(capacity=1, max_keys=2, clock=FakeClock())
    limiter.acquire("a"), limiter.acquire("b"), limiter.acquire("c")
    assert limiter.acquire("a").allowed  # "a" was evicted, so it starts full again


def test_thread_safety_never_over_admits():
    limiter = TokenBucketLimiter(capacity=100, period_seconds=3600)
    admitted = []
    lock = threading.Lock()

    def worker():
        for _ in range(50):
            if limiter.acquire("k").allowed:
                with lock:
                    admitted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(admitted) == 100


@pytest.mark.parametrize("capacity,period", [(0, 60), (5, 0)])
def test_rejects_invalid_configuration(capacity, period):
    with pytest.raises(ValueError):
        TokenBucketLimiter(capacity, period)

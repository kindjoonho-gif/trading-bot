"""Async token-bucket rate limiter.

Caps the rate of `acquire()` calls per second. Used by the rebalance executor
to keep parallel order submission under KIS's per-second per-endpoint cap
(~2/sec on mock, ~20/sec on real).
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float, capacity: int | None = None) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate}")
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(1, int(rate))
        self._tokens: float = float(self.capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1

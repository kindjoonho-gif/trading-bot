from __future__ import annotations

import asyncio
import time

import pytest

from trader.rebalance.rate_limiter import TokenBucket


@pytest.mark.asyncio
async def test_under_capacity_no_wait() -> None:
    bucket = TokenBucket(rate=10, capacity=5)
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    assert time.monotonic() - start < 0.05


@pytest.mark.asyncio
async def test_over_capacity_throttled() -> None:
    """6 acquires at rate=10/sec, capacity=5 should take >= 0.1s for the 6th."""
    bucket = TokenBucket(rate=10, capacity=5)
    start = time.monotonic()
    for _ in range(6):
        await bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.09, f"expected >= 0.09s, got {elapsed}"


@pytest.mark.asyncio
async def test_invalid_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0)
    with pytest.raises(ValueError):
        TokenBucket(rate=-1)


@pytest.mark.asyncio
async def test_parallel_acquires_capped_at_rate() -> None:
    """Three parallel acquires at rate=5/sec capacity=2 should take ~0.2s for the third."""
    bucket = TokenBucket(rate=5, capacity=2)
    start = time.monotonic()
    await asyncio.gather(*(bucket.acquire() for _ in range(3)))
    elapsed = time.monotonic() - start
    assert elapsed >= 0.18, f"expected >= 0.18s, got {elapsed}"

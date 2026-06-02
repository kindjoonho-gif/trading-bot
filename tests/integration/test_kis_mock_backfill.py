"""Live Backfill test against KIS Mock Account.

Gated by `KIS_INTEGRATION=1`. Reuses the shared token cache to avoid
EGW00133 (token issuance throttled at 1/min).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from trader.brokers.kis import KISBroker
from trader.config.settings import Settings
from trader.history.store import HistoryStore
from trader.history.sync import run_backfill

_RATE_LIMIT_SLEEP = 1.1
_KST = timezone(timedelta(hours=9))
_KRX_OPEN = time(9, 0)
_KRX_CLOSE = time(15, 30)


def _krx_open_now() -> bool:
    now = datetime.now(_KST)
    if now.weekday() >= 5:
        return False
    return _KRX_OPEN <= now.time() <= _KRX_CLOSE


_market_only = pytest.mark.skipif(
    not _krx_open_now(),
    reason="KRX regular trading hours only (Mon-Fri 09:00-15:30 KST)",
)

pytestmark = pytest.mark.skipif(
    os.environ.get("KIS_INTEGRATION") != "1",
    reason="set KIS_INTEGRATION=1 to run live mock backfill",
)


@pytest.fixture
def mock_settings() -> Settings:
    s = Settings()  # type: ignore[call-arg]
    if s.KIS_ENV != "mock":
        pytest.skip("integration test only runs with KIS_ENV=mock")
    return s


@pytest.fixture(scope="module")
def shared_cache() -> Path:
    cache = Path(__file__).resolve().parents[2] / ".cache"
    cache.mkdir(exist_ok=True)
    return cache


@pytest.mark.asyncio
@_market_only
async def test_backfill_idempotent_on_mock(
    mock_settings: Settings, shared_cache: Path, tmp_path: Path
) -> None:
    db_path = tmp_path / "history_mock.sqlite"
    store = HistoryStore(db_path)
    await store.connect()
    await store.apply_migrations()
    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        first = await run_backfill(broker, store, env="mock", window_days=1)
        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        second = await run_backfill(broker, store, env="mock", window_days=1)
    await store.close()

    assert first.pulled >= 0
    assert first.inserted == first.pulled
    assert first.already_present == 0

    assert second.pulled == first.pulled
    assert second.inserted == 0
    assert second.already_present == second.pulled

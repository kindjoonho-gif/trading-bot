"""Live handshake test against KIS Mock Account (모의투자).

Gated by `KIS_INTEGRATION=1` so it never runs by default.
Requires real `.env` credentials at the project root.

Token cache is module-scoped so all tests in this file share one issuance —
KIS throttles token issuance to 1/min (EGW00133), so per-test fresh caches fail.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from pathlib import Path

import pytest

from trader.brokers.kis import KISBroker
from trader.config.settings import Settings
from trader.domain.types import Symbol

_RATE_LIMIT_SLEEP = 1.1

pytestmark = pytest.mark.skipif(
    os.environ.get("KIS_INTEGRATION") != "1",
    reason="set KIS_INTEGRATION=1 to run live mock handshake",
)


@pytest.fixture
def mock_settings() -> Settings:
    s = Settings()  # type: ignore[call-arg]
    if s.KIS_ENV != "mock":
        pytest.skip("integration test only runs with KIS_ENV=mock")
    return s


@pytest.fixture(scope="module")
def shared_cache() -> Path:
    """Use the project's real `.cache/` so a still-valid token from a previous
    run is reused. KIS throttles fresh token issuance to 1/min (EGW00133)."""
    cache = Path(__file__).resolve().parents[2] / ".cache"
    cache.mkdir(exist_ok=True)
    return cache


@pytest.mark.asyncio
async def test_mock_auth_and_get_cash(mock_settings: Settings, shared_cache: Path) -> None:
    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        cash = await broker.get_cash()
        assert isinstance(cash, Decimal)
        assert cash >= 0
        assert (shared_cache / "kis_token_mock.json").exists()


@pytest.mark.asyncio
async def test_token_cache_hit_on_second_call(
    mock_settings: Settings, shared_cache: Path
) -> None:
    await asyncio.sleep(_RATE_LIMIT_SLEEP)
    async with KISBroker(mock_settings, cache_dir=shared_cache) as b1:
        await b1.get_cash()
    await asyncio.sleep(_RATE_LIMIT_SLEEP)
    async with KISBroker(mock_settings, cache_dir=shared_cache) as b2:
        await b2.get_cash()


@pytest.mark.asyncio
async def test_mock_get_positions(mock_settings: Settings, shared_cache: Path) -> None:
    await asyncio.sleep(_RATE_LIMIT_SLEEP)
    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        positions = await broker.get_positions()
        assert isinstance(positions, list)
        for p in positions:
            assert p.symbol
            assert p.quantity > 0
            assert p.avg_cost >= 0


@pytest.mark.asyncio
async def test_mock_get_quote_samsung(mock_settings: Settings, shared_cache: Path) -> None:
    await asyncio.sleep(_RATE_LIMIT_SLEEP)
    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        quote = await broker.get_quote(Symbol("005930"))
        assert quote.symbol == "005930"
        assert quote.last > 0
        assert quote.bid > 0
        assert quote.ask > 0
        assert quote.ask >= quote.bid

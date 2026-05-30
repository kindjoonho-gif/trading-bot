"""Live handshake test against KIS Mock Account (모의투자).

Gated by `KIS_INTEGRATION=1` so it never runs by default.
Requires real `.env` credentials at the project root.

Token cache is module-scoped so all tests in this file share one issuance —
KIS throttles token issuance to 1/min (EGW00133), so per-test fresh caches fail.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trader.brokers.kis import KISBroker
from trader.config.settings import Settings
from trader.domain.types import OrderKind, OrderStatus, Side, Symbol

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


@_market_only
@pytest.mark.asyncio
async def test_mock_place_and_get_market_buy_samsung(
    mock_settings: Settings, shared_cache: Path
) -> None:
    """Place a 1-share market buy on 005930 against KIS mock, poll until filled,
    assert 005930 appears in get_positions. Requires KRX regular trading hours."""

    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        order_id = await broker.place_order(
            Symbol("005930"), Side.BUY, OrderKind.MARKET, Decimal("1")
        )
        assert order_id

        terminal = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
        order = None
        for _ in range(10):
            await asyncio.sleep(_RATE_LIMIT_SLEEP)
            order = await broker.get_order(order_id)
            if order.status in terminal:
                break
        assert order is not None
        assert order.status is OrderStatus.FILLED, f"expected FILLED, got {order.status}"
        assert order.filled_quantity == Decimal("1")

        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        positions = await broker.get_positions()
        assert any(p.symbol == "005930" for p in positions), (
            f"005930 missing from positions after buy: {[p.symbol for p in positions]}"
        )

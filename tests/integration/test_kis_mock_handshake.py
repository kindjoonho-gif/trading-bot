"""Live handshake test against KIS Mock Account (모의투자).

Gated by `KIS_INTEGRATION=1` so it never runs by default.
Requires real `.env` credentials at the project root.

Token cache is module-scoped so all tests in this file share one issuance —
KIS throttles token issuance to 1/min (EGW00133), so per-test fresh caches fail.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from trader.brokers.kis import KISApiError, KISBroker
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


@_market_only
@pytest.mark.asyncio
async def test_mock_rebalance_execute_small_buy(
    mock_settings: Settings, shared_cache: Path
) -> None:
    """Build a tiny one-row Plan (buy 1 share of 005930), execute it against
    the mock broker via the executor + rate limiter, assert the outcome is
    classified as filled and the position lands."""
    from trader.domain.types import OrderKind
    from trader.rebalance.execute import execute
    from trader.rebalance.plan import Plan, PlanRow
    from trader.rebalance.rate_limiter import TokenBucket

    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        quote = await broker.get_quote(Symbol("005930"))

        row = PlanRow(
            symbol=Symbol("005930"),
            current_quantity=Decimal("0"),
            current_weight=Decimal("0"),
            target_weight=Decimal("0"),
            drift=Decimal("0"),
            raw_delta_won=Decimal("0"),
            raw_delta_shares=Decimal("1"),
            rounded_delta_shares=Decimal("1"),
            side=Side.BUY,
            kind=OrderKind.MARKET,
            skipped_reason=None,
        )
        plan_obj = Plan(
            rows=[row],
            total_value=quote.last,
            starting_cash=quote.last,
            cash_residual=Decimal("0"),
        )
        bucket = TokenBucket(rate=2.0)

        summary = await execute(plan_obj, broker, bucket, poll_interval=_RATE_LIMIT_SLEEP)
        assert len(summary.filled) == 1
        assert summary.filled[0].symbol == "005930"
        assert summary.filled[0].filled_quantity == Decimal("1")


@_market_only
@pytest.mark.asyncio
async def test_mock_rebalance_execute_forced_failure(
    mock_settings: Settings, shared_cache: Path
) -> None:
    """Force a failure path: sell 99999 shares of a symbol we don't hold.
    KIS should reject either at submit time or post-submit; either way the
    summary classifies it in the rejected bucket."""
    from trader.domain.types import OrderKind
    from trader.rebalance.execute import execute
    from trader.rebalance.plan import Plan, PlanRow
    from trader.rebalance.rate_limiter import TokenBucket

    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        row = PlanRow(
            symbol=Symbol("005930"),
            current_quantity=Decimal("0"),
            current_weight=Decimal("0"),
            target_weight=Decimal("0"),
            drift=Decimal("0"),
            raw_delta_won=Decimal("0"),
            raw_delta_shares=Decimal("-99999"),
            rounded_delta_shares=Decimal("-99999"),
            side=Side.SELL,
            kind=OrderKind.MARKET,
            skipped_reason=None,
        )
        plan_obj = Plan(
            rows=[row],
            total_value=Decimal("1"),
            starting_cash=Decimal("0"),
            cash_residual=Decimal("0"),
        )
        bucket = TokenBucket(rate=2.0)

        summary = await execute(plan_obj, broker, bucket, poll_interval=_RATE_LIMIT_SLEEP)
        assert len(summary.outcomes) == 1
        o = summary.outcomes[0]
        assert o.outcome in {"rejected", "errored"}, (
            f"expected rejected or errored for impossible sell, got {o.outcome}: {o.reason}"
        )
        assert len(summary.filled) == 0


@pytest.mark.asyncio
async def test_mock_list_fills_open_window(
    mock_settings: Settings, shared_cache: Path
) -> None:
    """Fetch fills for the last 7 days against KIS mock — body may be empty."""
    await asyncio.sleep(_RATE_LIMIT_SLEEP)
    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        end = date.today()
        start = end - timedelta(days=7)
        fills = await broker.list_fills(start, end)
        assert isinstance(fills, list)
        for f in fills:
            assert f.symbol
            assert f.quantity > 0
            assert f.fill_price > 0


@pytest.mark.asyncio
async def test_mock_realized_pnl_either_succeeds_or_rejects_cleanly(
    mock_settings: Settings, shared_cache: Path
) -> None:
    """KIS mock typically rejects TTTC8715R; if it does, the broker raises
    KISApiError instead of crashing in an opaque way. If mock happens to
    support it now, the call returns a typed summary either way."""
    await asyncio.sleep(_RATE_LIMIT_SLEEP)
    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        end = date.today()
        start = end - timedelta(days=30)
        try:
            summary = await broker.realized_pnl(start, end)
            assert summary.total_realized_pnl is not None
        except KISApiError as e:
            assert "EGW" in str(e) or "TR" in str(e) or "지원" in str(e), (
                f"unexpected error shape: {e}"
            )


@_market_only
@pytest.mark.asyncio
async def test_mock_list_then_cancel_limit_order(
    mock_settings: Settings, shared_cache: Path
) -> None:
    """Place a far-out-of-touch Limit buy on 005930 (will sit on the book),
    confirm it appears in list_open_orders, cancel it, confirm it disappears.
    Far-OTM price (1 KRW) so it cannot fill during the test window."""
    async with KISBroker(mock_settings, cache_dir=shared_cache) as broker:
        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        order_id = await broker.place_order(
            Symbol("005930"),
            Side.BUY,
            OrderKind.LIMIT,
            Decimal("1"),
            Decimal("1"),
        )
        assert order_id

        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        before = await broker.list_open_orders()
        assert any(o.order_id == order_id for o in before), (
            f"placed limit {order_id} missing from open orders: {[o.order_id for o in before]}"
        )

        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        await broker.cancel_order(order_id)

        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        after = await broker.list_open_orders()
        assert not any(o.order_id == order_id for o in after), (
            f"order {order_id} still in open list after cancel: {[o.order_id for o in after]}"
        )

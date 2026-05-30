from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from trader.brokers.kis import KISApiError
from trader.domain.types import (
    Order,
    OrderId,
    OrderKind,
    OrderStatus,
    Position,
    Quote,
    Side,
    Symbol,
)
from trader.rebalance.execute import RebalanceSummary, execute
from trader.rebalance.plan import plan
from trader.rebalance.rate_limiter import TokenBucket


class FakeBroker:
    """In-memory Broker double for execute() tests."""

    def __init__(
        self,
        *,
        reject_at_submit: set[Symbol] | None = None,
        reject_post_submit: set[Symbol] | None = None,
        error_at_submit: set[Symbol] | None = None,
    ) -> None:
        self.submit_times: list[float] = []
        self.placed: list[dict[str, Any]] = []
        self._reject_submit = reject_at_submit or set()
        self._reject_post = reject_post_submit or set()
        self._error_submit = error_at_submit or set()
        self._order_seq = 0
        self._orders: dict[OrderId, Order] = {}

    async def get_cash(self) -> Decimal:
        raise NotImplementedError

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError

    async def get_quote(self, symbol: Symbol) -> Quote:
        raise NotImplementedError

    async def place_order(
        self,
        symbol: Symbol,
        side: Side,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None = None,
    ) -> OrderId:
        self.submit_times.append(time.monotonic())
        self.placed.append({"symbol": symbol, "side": side, "qty": quantity})
        if symbol in self._reject_submit:
            raise KISApiError("FAKE001", f"forced submit reject for {symbol}")
        if symbol in self._error_submit:
            raise RuntimeError(f"forced local error for {symbol}")
        self._order_seq += 1
        oid = OrderId(f"FAKE{self._order_seq:06d}")
        status = OrderStatus.REJECTED if symbol in self._reject_post else OrderStatus.FILLED
        filled = Decimal("0") if status is OrderStatus.REJECTED else quantity
        self._orders[oid] = Order(
            order_id=oid,
            symbol=symbol,
            side=side,
            kind=kind,
            quantity=quantity,
            price=price,
            status=status,
            filled_quantity=filled,
            avg_fill_price=Decimal("100000") if filled > 0 else None,
        )
        return oid

    async def get_order(self, order_id: OrderId) -> Order:
        return self._orders[order_id]

    async def list_open_orders(self) -> list[Order]:
        raise NotImplementedError

    async def cancel_order(self, order_id: OrderId) -> None:
        raise NotImplementedError


def _quote(symbol: str, last: str) -> Quote:
    return Quote(
        symbol=Symbol(symbol), bid=Decimal(last), ask=Decimal(last), last=Decimal(last)
    )


def _make_plan(symbols: list[tuple[str, str]]) -> Any:
    """Build a Plan from (symbol, weight) pairs. All buys from cash."""
    positions: list[Position] = []
    quotes = {Symbol(s): _quote(s, "100000") for s, _ in symbols}
    cash = Decimal("10000000")
    targets = {Symbol(s): Decimal(w) for s, w in symbols}
    return plan(positions, cash, targets, quotes, Decimal("0"))


@pytest.fixture
def fast_bucket() -> TokenBucket:
    return TokenBucket(rate=1000, capacity=100)


@pytest.mark.asyncio
async def test_all_filled(fast_bucket: TokenBucket) -> None:
    p = _make_plan([("005930", "0.3"), ("000660", "0.2")])
    broker = FakeBroker()
    result = await execute(p, broker, fast_bucket, poll_interval=0)
    assert len(result.outcomes) == 2
    assert len(result.filled) == 2
    assert not result.rejected
    assert not result.errored
    for o in result.outcomes:
        assert o.filled_quantity == o.quantity
        assert o.order_id is not None


@pytest.mark.asyncio
async def test_skipped_rows_not_submitted(fast_bucket: TokenBucket) -> None:
    """Plan rows with skipped_reason set should be ignored entirely."""
    p = _make_plan([("005930", "0.000001")])
    broker = FakeBroker()
    result = await execute(p, broker, fast_bucket, poll_interval=0)
    assert result.outcomes == []
    assert broker.placed == []


@pytest.mark.asyncio
async def test_rejected_at_submit(fast_bucket: TokenBucket) -> None:
    p = _make_plan([("005930", "0.3"), ("000660", "0.2")])
    broker = FakeBroker(reject_at_submit={Symbol("005930")})
    result = await execute(p, broker, fast_bucket, poll_interval=0)
    by_sym = {o.symbol: o for o in result.outcomes}
    assert by_sym[Symbol("005930")].outcome == "rejected"
    assert "forced submit reject" in (by_sym[Symbol("005930")].reason or "")
    assert by_sym[Symbol("005930")].order_id is None
    assert by_sym[Symbol("000660")].outcome == "filled"


@pytest.mark.asyncio
async def test_rejected_post_submit(fast_bucket: TokenBucket) -> None:
    p = _make_plan([("005930", "0.3")])
    broker = FakeBroker(reject_post_submit={Symbol("005930")})
    result = await execute(p, broker, fast_bucket, poll_interval=0)
    o = result.outcomes[0]
    assert o.outcome == "rejected"
    assert o.order_id is not None
    assert "broker rejected after submit" in (o.reason or "")


@pytest.mark.asyncio
async def test_local_error_at_submit(fast_bucket: TokenBucket) -> None:
    p = _make_plan([("005930", "0.3")])
    broker = FakeBroker(error_at_submit={Symbol("005930")})
    result = await execute(p, broker, fast_bucket, poll_interval=0)
    o = result.outcomes[0]
    assert o.outcome == "errored"
    assert "RuntimeError" in (o.reason or "")


@pytest.mark.asyncio
async def test_empty_plan() -> None:
    p = _make_plan([])
    bucket = TokenBucket(rate=1000)
    result = await execute(p, FakeBroker(), bucket, poll_interval=0)
    assert result.outcomes == []


@pytest.mark.asyncio
async def test_summary_buckets() -> None:
    p = _make_plan([("005930", "0.2"), ("000660", "0.2"), ("035420", "0.2")])
    broker = FakeBroker(
        reject_at_submit={Symbol("000660")}, error_at_submit={Symbol("035420")}
    )
    bucket = TokenBucket(rate=1000)
    result = await execute(p, broker, bucket, poll_interval=0)
    assert len(result.filled) == 1
    assert len(result.rejected) == 1
    assert len(result.errored) == 1


@pytest.mark.asyncio
async def test_rate_limiter_throttles_parallel_submissions() -> None:
    """3 submissions at rate=5/sec capacity=2 should take ~0.2s for the third token."""
    p = _make_plan([("005930", "0.2"), ("000660", "0.2"), ("035420", "0.2")])
    broker = FakeBroker()
    bucket = TokenBucket(rate=5, capacity=2)
    start = time.monotonic()
    result = await execute(p, broker, bucket, poll_interval=0)
    elapsed = time.monotonic() - start
    assert len(result.filled) == 3
    assert elapsed >= 0.18, f"expected throttle, took only {elapsed}s"


def test_summary_default_empty() -> None:
    s = RebalanceSummary()
    assert s.outcomes == []
    assert s.filled == []
    assert s.rejected == []
    assert s.errored == []

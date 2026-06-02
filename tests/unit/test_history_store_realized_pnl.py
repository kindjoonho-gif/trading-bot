from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from trader.domain.types import OrderId, Side, Symbol, Trade
from trader.history.store import HistoryStore


def _trade(
    *,
    odno: str,
    side: Side,
    quantity: str,
    avg_price: str,
    symbol: str = "005930",
    ord_date: date = date(2026, 6, 1),
    ord_time: str = "100000",
) -> Trade:
    return Trade(
        odno=OrderId(odno),
        ord_date=ord_date,
        ord_time=ord_time,
        symbol=Symbol(symbol),
        side=side,
        quantity=Decimal(quantity),
        avg_price=Decimal(avg_price),
    )


@pytest.fixture
async def store() -> HistoryStore:
    s = HistoryStore(":memory:")
    await s.connect()
    await s.apply_migrations()
    return s


class TestRealizedPnLWrap:
    async def test_empty_window_returns_empty_report(self, store: HistoryStore) -> None:
        r = await store.realized_pnl(date(2026, 6, 1), date(2026, 6, 30))
        assert r.rows == ()
        assert r.unmatched_sells == ()
        assert r.total_realized_pnl == Decimal("0")
        await store.close()

    async def test_matches_fifo_over_stored_trades(self, store: HistoryStore) -> None:
        await store.upsert_trades(
            [
                _trade(odno="B1", side=Side.BUY, quantity="10", avg_price="70000",
                       ord_time="090000"),
                _trade(odno="S1", side=Side.SELL, quantity="10", avg_price="75000",
                       ord_time="100000"),
            ]
        )
        r = await store.realized_pnl(date(2026, 6, 1), date(2026, 6, 1))
        assert len(r.rows) == 1
        assert r.rows[0].realized_pnl == Decimal("50000")
        assert r.unmatched_sells == ()
        await store.close()

    async def test_excludes_trades_outside_window(self, store: HistoryStore) -> None:
        await store.upsert_trades(
            [
                _trade(odno="B1", side=Side.BUY, quantity="10", avg_price="70000",
                       ord_date=date(2026, 5, 30)),
                _trade(odno="S1", side=Side.SELL, quantity="10", avg_price="75000",
                       ord_date=date(2026, 6, 5)),
            ]
        )
        r = await store.realized_pnl(date(2026, 6, 1), date(2026, 6, 10))
        assert r.rows == ()
        assert len(r.unmatched_sells) == 1
        await store.close()

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from trader.domain.types import OrderId, Side, Symbol, Trade
from trader.history.store import HistoryStore


def _trade(
    *,
    odno: str = "0001",
    ord_date: date = date(2026, 6, 1),
    ord_time: str = "100000",
    symbol: str = "005930",
    side: Side = Side.BUY,
    quantity: str = "10",
    avg_price: str = "70000",
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


class TestApplyMigrations:
    async def test_fresh_db_applies_initial_migration(self) -> None:
        s = HistoryStore(":memory:")
        await s.connect()
        applied = await s.apply_migrations()
        assert applied == 1
        await s.close()

    async def test_second_call_is_noop(self, store: HistoryStore) -> None:
        applied = await store.apply_migrations()
        assert applied == 0
        await store.close()


class TestUpsertTrades:
    async def test_insert_then_list(self, store: HistoryStore) -> None:
        t = _trade()
        inserted = await store.upsert_trades([t])
        assert inserted == 1
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 1))
        assert rows == [t]
        await store.close()

    async def test_duplicate_odno_ord_date_ignored(self, store: HistoryStore) -> None:
        t = _trade()
        first = await store.upsert_trades([t])
        second = await store.upsert_trades([t])
        assert first == 1
        assert second == 0
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 1))
        assert len(rows) == 1
        await store.close()

    async def test_same_odno_different_date_is_distinct(self, store: HistoryStore) -> None:
        t1 = _trade(odno="0001", ord_date=date(2026, 6, 1))
        t2 = _trade(odno="0001", ord_date=date(2026, 6, 2))
        inserted = await store.upsert_trades([t1, t2])
        assert inserted == 2
        await store.close()

    async def test_empty_list_returns_zero(self, store: HistoryStore) -> None:
        assert await store.upsert_trades([]) == 0
        await store.close()

    async def test_decimal_round_trip_preserves_precision(self, store: HistoryStore) -> None:
        t = _trade(quantity="1.5", avg_price="70123.4567")
        await store.upsert_trades([t])
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 1))
        assert rows[0].quantity == Decimal("1.5")
        assert rows[0].avg_price == Decimal("70123.4567")
        await store.close()

    async def test_mixed_batch_counts_only_new_rows(self, store: HistoryStore) -> None:
        t1 = _trade(odno="A")
        t2 = _trade(odno="B")
        await store.upsert_trades([t1])
        inserted = await store.upsert_trades([t1, t2])
        assert inserted == 1
        await store.close()


class TestListTrades:
    async def test_empty_db_returns_empty(self, store: HistoryStore) -> None:
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 30))
        assert rows == []
        await store.close()

    async def test_date_window_filters(self, store: HistoryStore) -> None:
        outside_before = _trade(odno="A", ord_date=date(2026, 5, 31))
        inside_a = _trade(odno="B", ord_date=date(2026, 6, 1), ord_time="090000")
        inside_b = _trade(odno="C", ord_date=date(2026, 6, 15), ord_time="100000")
        outside_after = _trade(odno="D", ord_date=date(2026, 7, 1))
        await store.upsert_trades([outside_before, inside_a, inside_b, outside_after])
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 30))
        assert [t.odno for t in rows] == ["B", "C"]
        await store.close()

    async def test_ordering_chronological(self, store: HistoryStore) -> None:
        a = _trade(odno="A", ord_date=date(2026, 6, 1), ord_time="153000")
        b = _trade(odno="B", ord_date=date(2026, 6, 1), ord_time="090000")
        c = _trade(odno="C", ord_date=date(2026, 6, 2), ord_time="100000")
        await store.upsert_trades([a, b, c])
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 30))
        assert [t.odno for t in rows] == ["B", "A", "C"]
        await store.close()

    async def test_multi_symbol_isolated(self, store: HistoryStore) -> None:
        samsung = _trade(odno="A", symbol="005930")
        kakao = _trade(odno="B", symbol="035720")
        await store.upsert_trades([samsung, kakao])
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 1))
        symbols = {t.symbol for t in rows}
        assert symbols == {Symbol("005930"), Symbol("035720")}
        await store.close()


class TestConnectionLifecycle:
    async def test_methods_before_connect_raise(self) -> None:
        s = HistoryStore(":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            await s.list_trades(date(2026, 6, 1), date(2026, 6, 1))

    async def test_close_is_idempotent(self) -> None:
        s = HistoryStore(":memory:")
        await s.connect()
        await s.close()
        await s.close()

    async def test_connect_is_idempotent(self) -> None:
        s = HistoryStore(":memory:")
        await s.connect()
        await s.connect()
        await s.close()

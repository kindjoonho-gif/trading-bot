from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trader.domain.types import Fill, OrderId, Side, Symbol
from trader.history.store import HistoryStore
from trader.history.sync import run_backfill

_KST = timezone(timedelta(hours=9))


class FakeBroker:
    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills
        self.calls: list[tuple[date, date]] = []

    async def list_fills(self, start_date: date, end_date: date) -> list[Fill]:
        self.calls.append((start_date, end_date))
        return [
            f for f in self._fills if start_date <= f.fill_time.date() <= end_date
        ]


def _fill(
    *,
    odno: str = "0001",
    pdno: str = "005930",
    side: Side = Side.BUY,
    qty: str = "10",
    price: str = "70000",
    when: datetime | None = None,
) -> Fill:
    return Fill(
        symbol=Symbol(pdno),
        side=side,
        quantity=Decimal(qty),
        fill_price=Decimal(price),
        fill_time=when or datetime(2026, 6, 1, 10, 0, 0, tzinfo=_KST),
        fees=Decimal("0"),
        odno=OrderId(odno),
    )


@pytest.fixture
async def store() -> HistoryStore:
    s = HistoryStore(":memory:")
    await s.connect()
    await s.apply_migrations()
    return s


class TestWindowSelection:
    async def test_empty_store_mock_uses_1_day(self, store: HistoryStore) -> None:
        broker = FakeBroker([])
        await run_backfill(broker, store, env="mock", today=date(2026, 6, 1))
        assert broker.calls == [(date(2026, 6, 1), date(2026, 6, 1))]
        await store.close()

    async def test_empty_store_real_uses_90_days(self, store: HistoryStore) -> None:
        broker = FakeBroker([])
        await run_backfill(broker, store, env="real", today=date(2026, 6, 1))
        start, end = broker.calls[0]
        assert end == date(2026, 6, 1)
        assert (end - start).days == 89
        await store.close()

    async def test_non_empty_store_uses_7_days(self, store: HistoryStore) -> None:
        f = _fill(when=datetime(2026, 5, 1, 10, 0, 0, tzinfo=_KST))
        broker = FakeBroker([f])
        await run_backfill(broker, store, env="mock", today=date(2026, 5, 1))
        broker.calls.clear()
        await run_backfill(broker, store, env="mock", today=date(2026, 6, 1))
        start, end = broker.calls[0]
        assert end == date(2026, 6, 1)
        assert (end - start).days == 6
        await store.close()

    async def test_explicit_window_days_overrides(self, store: HistoryStore) -> None:
        broker = FakeBroker([])
        await run_backfill(
            broker, store, env="real", window_days=30, today=date(2026, 6, 1)
        )
        start, end = broker.calls[0]
        assert (end - start).days == 29
        await store.close()


class TestIdempotence:
    async def test_second_run_inserts_zero(self, store: HistoryStore) -> None:
        f = _fill(when=datetime(2026, 6, 1, 10, 0, 0, tzinfo=_KST))
        broker = FakeBroker([f])
        first = await run_backfill(broker, store, env="mock", today=date(2026, 6, 1))
        second = await run_backfill(broker, store, env="mock", today=date(2026, 6, 1))
        assert first.pulled == 1
        assert first.inserted == 1
        assert first.already_present == 0
        assert second.pulled == 1
        assert second.inserted == 0
        assert second.already_present == 1
        await store.close()


class TestSummaryCounts:
    async def test_mixed_existing_and_new(self, store: HistoryStore) -> None:
        old = _fill(odno="OLD", when=datetime(2026, 5, 28, 10, 0, 0, tzinfo=_KST))
        broker_old = FakeBroker([old])
        await run_backfill(broker_old, store, env="mock", today=date(2026, 5, 28))
        new = _fill(odno="NEW", when=datetime(2026, 6, 1, 10, 0, 0, tzinfo=_KST))
        broker_both = FakeBroker([old, new])
        summary = await run_backfill(
            broker_both, store, env="mock", window_days=7, today=date(2026, 6, 1)
        )
        assert summary.pulled == 2
        assert summary.inserted == 1
        assert summary.already_present == 1
        await store.close()


class TestFillToTrade:
    async def test_converts_fields_and_persists(self, store: HistoryStore) -> None:
        f = _fill(
            odno="ABC",
            pdno="035720",
            side=Side.SELL,
            qty="3.5",
            price="42000",
            when=datetime(2026, 6, 1, 9, 15, 30, tzinfo=_KST),
        )
        broker = FakeBroker([f])
        await run_backfill(broker, store, env="mock", today=date(2026, 6, 1))
        rows = await store.list_trades(date(2026, 6, 1), date(2026, 6, 1))
        assert len(rows) == 1
        t = rows[0]
        assert t.odno == OrderId("ABC")
        assert t.symbol == Symbol("035720")
        assert t.side is Side.SELL
        assert t.quantity == Decimal("3.5")
        assert t.avg_price == Decimal("42000")
        assert t.ord_date == date(2026, 6, 1)
        assert t.ord_time == "091530"
        await store.close()

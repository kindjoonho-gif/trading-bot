from __future__ import annotations

from datetime import date
from decimal import Decimal

from trader.domain.types import OrderId, Side, Symbol, Trade
from trader.history.pnl import compute_realized_pnl

_T_BASE = date(2026, 6, 1)


def _t(
    *,
    side: Side,
    quantity: str,
    avg_price: str,
    symbol: str = "005930",
    ord_date: date = _T_BASE,
    ord_time: str = "100000",
    odno: str = "0001",
) -> Trade:
    return Trade(
        symbol=Symbol(symbol),
        side=side,
        quantity=Decimal(quantity),
        avg_price=Decimal(avg_price),
        ord_date=ord_date,
        ord_time=ord_time,
        odno=OrderId(odno),
    )


class TestEmpty:
    def test_zero_trades_returns_empty_report(self) -> None:
        r = compute_realized_pnl([])
        assert r.rows == ()
        assert r.unmatched_sells == ()
        assert r.total_buy_amount == Decimal("0")
        assert r.total_sell_amount == Decimal("0")
        assert r.total_realized_pnl == Decimal("0")


class TestSingleBuySingleSell:
    def test_exact_match(self) -> None:
        trades = [
            _t(side=Side.BUY, quantity="10", avg_price="70000", odno="B1", ord_time="090000"),
            _t(side=Side.SELL, quantity="10", avg_price="75000", odno="S1", ord_time="100000"),
        ]
        r = compute_realized_pnl(trades)
        assert r.unmatched_sells == ()
        assert len(r.rows) == 1
        row = r.rows[0]
        assert row.quantity == Decimal("10")
        assert row.buy_amount == Decimal("700000")
        assert row.sell_amount == Decimal("750000")
        assert row.realized_pnl == Decimal("50000")
        assert row.return_pct == Decimal("50000") / Decimal("700000")
        assert r.total_realized_pnl == Decimal("50000")


class TestPartialSell:
    def test_partial_sell_consumes_part_of_one_buy(self) -> None:
        trades = [
            _t(side=Side.BUY, quantity="10", avg_price="70000", odno="B1", ord_time="090000"),
            _t(side=Side.SELL, quantity="3", avg_price="75000", odno="S1", ord_time="100000"),
        ]
        r = compute_realized_pnl(trades)
        assert r.unmatched_sells == ()
        row = r.rows[0]
        assert row.quantity == Decimal("3")
        assert row.buy_amount == Decimal("210000")
        assert row.sell_amount == Decimal("225000")
        assert row.realized_pnl == Decimal("15000")


class TestSellSpansBuys:
    def test_fifo_consumes_oldest_buy_first(self) -> None:
        trades = [
            _t(side=Side.BUY, quantity="5", avg_price="60000", odno="B1", ord_time="090000"),
            _t(side=Side.BUY, quantity="5", avg_price="80000", odno="B2", ord_time="091500"),
            _t(side=Side.SELL, quantity="7", avg_price="90000", odno="S1", ord_time="100000"),
        ]
        r = compute_realized_pnl(trades)
        assert r.unmatched_sells == ()
        row = r.rows[0]
        assert row.quantity == Decimal("7")
        assert row.buy_amount == Decimal("5") * Decimal("60000") + Decimal("2") * Decimal("80000")
        assert row.sell_amount == Decimal("7") * Decimal("90000")
        assert row.realized_pnl == row.sell_amount - row.buy_amount


class TestUnmatchedSells:
    def test_sell_without_buy_is_unmatched(self) -> None:
        trades = [
            _t(side=Side.SELL, quantity="3", avg_price="75000", odno="S1"),
        ]
        r = compute_realized_pnl(trades)
        assert r.rows == ()
        assert len(r.unmatched_sells) == 1
        leg = r.unmatched_sells[0]
        assert leg.quantity == Decimal("3")
        assert leg.avg_price == Decimal("75000")
        assert leg.odno == OrderId("S1")
        assert r.total_realized_pnl == Decimal("0")

    def test_sell_exceeds_buys_emits_unmatched_remainder(self) -> None:
        trades = [
            _t(side=Side.BUY, quantity="5", avg_price="70000", odno="B1", ord_time="090000"),
            _t(side=Side.SELL, quantity="8", avg_price="75000", odno="S1", ord_time="100000"),
        ]
        r = compute_realized_pnl(trades)
        assert len(r.rows) == 1
        assert r.rows[0].quantity == Decimal("5")
        assert len(r.unmatched_sells) == 1
        assert r.unmatched_sells[0].quantity == Decimal("3")
        assert r.unmatched_sells[0].avg_price == Decimal("75000")

    def test_buy_after_unmatched_sell_does_not_back_fill(self) -> None:
        trades = [
            _t(side=Side.SELL, quantity="3", avg_price="75000", odno="S1", ord_time="090000"),
            _t(side=Side.BUY, quantity="3", avg_price="70000", odno="B1", ord_time="100000"),
        ]
        r = compute_realized_pnl(trades)
        assert r.rows == ()
        assert len(r.unmatched_sells) == 1


class TestOpenPosition:
    def test_buy_with_no_sell_yields_no_realized(self) -> None:
        trades = [_t(side=Side.BUY, quantity="10", avg_price="70000", odno="B1")]
        r = compute_realized_pnl(trades)
        assert r.rows == ()
        assert r.unmatched_sells == ()
        assert r.total_realized_pnl == Decimal("0")


class TestMultiSymbol:
    def test_symbols_isolated(self) -> None:
        trades = [
            _t(side=Side.BUY, symbol="005930", quantity="10", avg_price="70000", odno="B1"),
            _t(side=Side.BUY, symbol="035720", quantity="5", avg_price="40000", odno="B2"),
            _t(side=Side.SELL, symbol="005930", quantity="10", avg_price="75000", odno="S1",
               ord_time="110000"),
            _t(side=Side.SELL, symbol="035720", quantity="5", avg_price="38000", odno="S2",
               ord_time="120000"),
        ]
        r = compute_realized_pnl(trades)
        assert r.unmatched_sells == ()
        assert len(r.rows) == 2
        rows_by_symbol = {row.symbol: row for row in r.rows}
        assert rows_by_symbol[Symbol("005930")].realized_pnl == Decimal("50000")
        assert rows_by_symbol[Symbol("035720")].realized_pnl == Decimal("-10000")
        assert r.total_realized_pnl == Decimal("40000")

    def test_unmatched_sell_on_one_symbol_does_not_affect_other(self) -> None:
        trades = [
            _t(side=Side.BUY, symbol="005930", quantity="10", avg_price="70000", odno="B1"),
            _t(side=Side.SELL, symbol="005930", quantity="10", avg_price="75000", odno="S1",
               ord_time="110000"),
            _t(side=Side.SELL, symbol="035720", quantity="5", avg_price="40000", odno="S2",
               ord_time="100000"),
        ]
        r = compute_realized_pnl(trades)
        assert len(r.rows) == 1
        assert r.rows[0].symbol == Symbol("005930")
        assert len(r.unmatched_sells) == 1
        assert r.unmatched_sells[0].symbol == Symbol("035720")


class TestOrdering:
    def test_input_order_does_not_change_result(self) -> None:
        a = _t(side=Side.BUY, quantity="5", avg_price="60000", odno="B1", ord_time="090000")
        b = _t(side=Side.BUY, quantity="5", avg_price="80000", odno="B2", ord_time="091500")
        s = _t(side=Side.SELL, quantity="7", avg_price="90000", odno="S1", ord_time="100000")
        forward = compute_realized_pnl([a, b, s])
        reversed_ = compute_realized_pnl([s, b, a])
        assert forward == reversed_

    def test_chronological_across_dates(self) -> None:
        b1 = _t(
            side=Side.BUY,
            quantity="5",
            avg_price="60000",
            odno="B1",
            ord_date=date(2026, 5, 30),
        )
        b2 = _t(
            side=Side.BUY,
            quantity="5",
            avg_price="80000",
            odno="B2",
            ord_date=date(2026, 5, 31),
        )
        s = _t(
            side=Side.SELL,
            quantity="7",
            avg_price="90000",
            odno="S1",
            ord_date=date(2026, 6, 1),
        )
        r = compute_realized_pnl([s, b2, b1])
        assert r.unmatched_sells == ()
        assert r.rows[0].buy_amount == (
            Decimal("5") * Decimal("60000") + Decimal("2") * Decimal("80000")
        )

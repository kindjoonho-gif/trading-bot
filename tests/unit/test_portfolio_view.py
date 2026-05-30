from __future__ import annotations

from decimal import Decimal

from trader.domain.types import Position, Quote, Symbol
from trader.portfolio.view import build_rows


def _q(symbol: str, last: str) -> Quote:
    return Quote(
        symbol=Symbol(symbol),
        bid=Decimal(last),
        ask=Decimal(last),
        last=Decimal(last),
    )


def _p(symbol: str, qty: str, avg: str) -> Position:
    return Position(symbol=Symbol(symbol), quantity=Decimal(qty), avg_cost=Decimal(avg))


def test_empty_positions_returns_total_eq_cash() -> None:
    rows, total = build_rows([], {}, Decimal("1000000"))
    assert rows == []
    assert total == Decimal("1000000")


def test_single_position_pnl_and_weight() -> None:
    positions = [_p("005930", "10", "70000")]
    quotes = {Symbol("005930"): _q("005930", "71000")}
    rows, total = build_rows(positions, quotes, Decimal("300000"))
    assert total == Decimal("710000") + Decimal("300000")
    assert len(rows) == 1
    r = rows[0]
    assert r.market_value == Decimal("710000")
    assert r.unrealized_pnl == Decimal("10000")
    assert r.weight == Decimal("710000") / Decimal("1010000")


def test_multiple_positions_weights_sum_to_one_minus_cash_share() -> None:
    positions = [
        _p("005930", "10", "70000"),
        _p("000660", "5", "120000"),
    ]
    quotes = {
        Symbol("005930"): _q("005930", "70000"),
        Symbol("000660"): _q("000660", "120000"),
    }
    cash = Decimal("100000")
    rows, total = build_rows(positions, quotes, cash)
    assert total == Decimal("700000") + Decimal("600000") + cash
    summed = sum((r.weight for r in rows), start=Decimal("0"))
    cash_weight = cash / total
    assert summed + cash_weight == Decimal("1")


def test_negative_pnl_for_loss() -> None:
    positions = [_p("005930", "10", "80000")]
    quotes = {Symbol("005930"): _q("005930", "70000")}
    rows, _ = build_rows(positions, quotes, Decimal("0"))
    assert rows[0].unrealized_pnl == Decimal("-100000")


def test_zero_total_value_yields_zero_weights() -> None:
    positions = [_p("005930", "10", "70000")]
    quotes = {Symbol("005930"): _q("005930", "0")}
    rows, total = build_rows(positions, quotes, Decimal("0"))
    assert total == Decimal("0")
    assert rows[0].weight == Decimal("0")

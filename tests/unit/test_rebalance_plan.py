from __future__ import annotations

from decimal import Decimal

from trader.domain.types import OrderKind, Position, Quote, Side, Symbol
from trader.rebalance.plan import plan


def _p(symbol: str, qty: str, avg: str = "0") -> Position:
    return Position(symbol=Symbol(symbol), quantity=Decimal(qty), avg_cost=Decimal(avg))


def _q(symbol: str, last: str) -> Quote:
    return Quote(
        symbol=Symbol(symbol),
        bid=Decimal(last),
        ask=Decimal(last),
        last=Decimal(last),
    )


def _t(*pairs: tuple[str, str]) -> dict[Symbol, Decimal]:
    return {Symbol(s): Decimal(w) for s, w in pairs}


_TOL = Decimal("0.01")
_ZERO_TOL = Decimal("0")


def test_exact_fit_no_orders() -> None:
    """Current weights == target weights exactly => all skipped, no cash delta."""
    positions = [_p("005930", "10")]
    quotes = {Symbol("005930"): _q("005930", "70000")}
    cash = Decimal("300000")
    targets = _t(("005930", "0.7"))
    result = plan(positions, cash, targets, quotes, _TOL)
    assert result.total_value == Decimal("1000000")
    assert len(result.rows) == 1
    r = result.rows[0]
    assert r.current_weight == Decimal("0.7")
    assert r.drift == Decimal("0")
    assert r.rounded_delta_shares == Decimal("0")
    assert r.skipped_reason == "below_tolerance"
    assert result.cash_residual == cash


def test_residual_cash_when_weights_sum_under_one() -> None:
    positions: list[Position] = []
    quotes = {Symbol("005930"): _q("005930", "100000")}
    cash = Decimal("1000000")
    targets = _t(("005930", "0.5"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    assert result.total_value == Decimal("1000000")
    r = result.rows[0]
    assert r.rounded_delta_shares == Decimal("5")
    assert r.side is Side.BUY
    assert result.cash_residual == Decimal("500000")


def test_below_tolerance_skip() -> None:
    """Drift smaller than tolerance is skipped."""
    positions = [_p("005930", "10")]
    quotes = {Symbol("005930"): _q("005930", "70000")}
    cash = Decimal("300000")
    targets = _t(("005930", "0.705"))
    result = plan(positions, cash, targets, quotes, _TOL)
    r = result.rows[0]
    assert abs(r.drift) < _TOL
    assert r.skipped_reason == "below_tolerance"


def test_round_toward_zero_buy_side() -> None:
    """Raw delta 2.7 shares rounds DOWN to 2 (positive -> round toward zero)."""
    positions: list[Position] = []
    quotes = {Symbol("005930"): _q("005930", "70000")}
    cash = Decimal("1000000")
    targets = _t(("005930", "0.19"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    r = result.rows[0]
    assert r.raw_delta_shares > Decimal("2")
    assert r.raw_delta_shares < Decimal("3")
    assert r.rounded_delta_shares == Decimal("2")
    assert r.side is Side.BUY


def test_round_toward_zero_sell_side() -> None:
    """Raw delta -2.7 shares rounds UP to -2 (negative -> round toward zero)."""
    positions = [_p("005930", "10")]
    quotes = {Symbol("005930"): _q("005930", "70000")}
    cash = Decimal("300000")
    targets = _t(("005930", "0.51"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    r = result.rows[0]
    assert r.raw_delta_shares < Decimal("-2")
    assert r.raw_delta_shares > Decimal("-3")
    assert r.rounded_delta_shares == Decimal("-2")
    assert r.side is Side.SELL


def test_single_symbol_portfolio() -> None:
    positions: list[Position] = []
    quotes = {Symbol("005930"): _q("005930", "100000")}
    cash = Decimal("1000000")
    targets = _t(("005930", "1.0"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    assert len(result.rows) == 1
    r = result.rows[0]
    assert r.rounded_delta_shares == Decimal("10")


def test_all_cash_portfolio() -> None:
    """Empty target_weights -> empty plan, cash residual = cash."""
    positions = [_p("005930", "10")]
    quotes = {Symbol("005930"): _q("005930", "70000")}
    cash = Decimal("300000")
    result = plan(positions, cash, {}, quotes, _TOL)
    assert result.rows == []
    assert result.total_value == Decimal("1000000")
    assert result.cash_residual == cash


def test_weights_sum_exactly_one() -> None:
    positions = [_p("005930", "5"), _p("000660", "3")]
    quotes = {
        Symbol("005930"): _q("005930", "100000"),
        Symbol("000660"): _q("000660", "100000"),
    }
    cash = Decimal("200000")
    targets = _t(("005930", "0.5"), ("000660", "0.5"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    by_symbol = {r.symbol: r for r in result.rows}
    assert by_symbol[Symbol("005930")].rounded_delta_shares == Decimal("0")
    assert by_symbol[Symbol("000660")].rounded_delta_shares == Decimal("2")
    assert result.cash_residual == Decimal("0")


def test_target_zero_full_liquidation() -> None:
    positions = [_p("005930", "10")]
    quotes = {Symbol("005930"): _q("005930", "70000")}
    cash = Decimal("300000")
    targets = _t(("005930", "0"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    r = result.rows[0]
    assert r.rounded_delta_shares == Decimal("-10")
    assert r.side is Side.SELL
    assert r.skipped_reason is None
    assert result.cash_residual == Decimal("300000") + Decimal("700000")


def test_idempotency_on_unchanged_inputs() -> None:
    """Re-running plan on the post-execution state should be a no-op."""
    positions = [_p("005930", "5"), _p("000660", "3")]
    quotes = {
        Symbol("005930"): _q("005930", "100000"),
        Symbol("000660"): _q("000660", "100000"),
    }
    cash = Decimal("200000")
    targets = _t(("005930", "0.5"), ("000660", "0.3"))
    result1 = plan(positions, cash, targets, quotes, _ZERO_TOL)
    result2 = plan(positions, cash, targets, quotes, _ZERO_TOL)
    assert result1 == result2


def test_held_but_not_in_target_is_ignored() -> None:
    """Symbols held but missing from target_weights are not in the plan."""
    positions = [_p("005930", "10"), _p("000660", "5")]
    quotes = {
        Symbol("005930"): _q("005930", "70000"),
        Symbol("000660"): _q("000660", "120000"),
    }
    cash = Decimal("300000")
    targets = _t(("005930", "0.7"))
    result = plan(positions, cash, targets, quotes, _TOL)
    assert {r.symbol for r in result.rows} == {Symbol("005930")}


def test_rounds_to_zero_distinct_from_tolerance_skip() -> None:
    """Drift > tolerance but rounded delta == 0 => skipped_reason='rounds_to_zero'."""
    positions: list[Position] = []
    quotes = {Symbol("005930"): _q("005930", "100000")}
    cash = Decimal("10000")
    targets = _t(("005930", "0.5"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    r = result.rows[0]
    assert abs(r.drift) > _ZERO_TOL
    assert r.raw_delta_shares > Decimal("0")
    assert r.raw_delta_shares < Decimal("1")
    assert r.rounded_delta_shares == Decimal("0")
    assert r.skipped_reason == "rounds_to_zero"


def test_kind_always_market_for_all_rows() -> None:
    positions: list[Position] = []
    quotes = {Symbol("005930"): _q("005930", "100000")}
    cash = Decimal("1000000")
    targets = _t(("005930", "0.5"))
    result = plan(positions, cash, targets, quotes, _ZERO_TOL)
    for r in result.rows:
        assert r.kind is OrderKind.MARKET

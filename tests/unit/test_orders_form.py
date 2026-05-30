from __future__ import annotations

from decimal import Decimal

import pytest

from trader.domain.types import OrderKind, Side
from trader.orders.form import OrderFormError, validate_order_form


def test_market_buy_happy_path() -> None:
    req = validate_order_form("005930", Side.BUY, OrderKind.MARKET, Decimal("1"), None)
    assert req.symbol == "005930"
    assert req.side is Side.BUY
    assert req.kind is OrderKind.MARKET
    assert req.quantity == Decimal("1")
    assert req.price is None


def test_limit_sell_happy_path() -> None:
    req = validate_order_form("005930", Side.SELL, OrderKind.LIMIT, Decimal("5"), Decimal("70000"))
    assert req.kind is OrderKind.LIMIT
    assert req.price == Decimal("70000")


def test_market_with_price_rejected() -> None:
    with pytest.raises(OrderFormError, match="Market"):
        validate_order_form("005930", Side.BUY, OrderKind.MARKET, Decimal("1"), Decimal("70000"))


def test_limit_without_price_rejected() -> None:
    with pytest.raises(OrderFormError, match="Limit"):
        validate_order_form("005930", Side.BUY, OrderKind.LIMIT, Decimal("1"), None)


def test_limit_zero_price_rejected() -> None:
    with pytest.raises(OrderFormError, match="Limit"):
        validate_order_form("005930", Side.BUY, OrderKind.LIMIT, Decimal("1"), Decimal("0"))


def test_empty_symbol_rejected() -> None:
    with pytest.raises(OrderFormError, match="Symbol"):
        validate_order_form("  ", Side.BUY, OrderKind.MARKET, Decimal("1"), None)


def test_zero_quantity_rejected() -> None:
    with pytest.raises(OrderFormError, match="Quantity"):
        validate_order_form("005930", Side.BUY, OrderKind.MARKET, Decimal("0"), None)


def test_fractional_quantity_rejected() -> None:
    with pytest.raises(OrderFormError, match="whole"):
        validate_order_form("005930", Side.BUY, OrderKind.MARKET, Decimal("1.5"), None)


def test_symbol_trimmed() -> None:
    req = validate_order_form("  005930  ", Side.BUY, OrderKind.MARKET, Decimal("1"), None)
    assert req.symbol == "005930"

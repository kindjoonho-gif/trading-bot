from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trader.domain.types import OrderKind, Side, Symbol


class OrderFormError(ValueError):
    pass


@dataclass(frozen=True)
class OrderRequest:
    symbol: Symbol
    side: Side
    kind: OrderKind
    quantity: Decimal
    price: Decimal | None


def validate_order_form(
    symbol: str,
    side: Side,
    kind: OrderKind,
    quantity: Decimal,
    price: Decimal | None,
) -> OrderRequest:
    """Pure validator for the Place Order form. Raises OrderFormError on bad combos."""
    if not symbol or not symbol.strip():
        raise OrderFormError("Symbol is required")
    if quantity <= 0:
        raise OrderFormError("Quantity must be > 0")
    if quantity != quantity.to_integral_value():
        raise OrderFormError("Quantity must be a whole number of shares")
    if kind is OrderKind.MARKET and price is not None:
        raise OrderFormError("Market orders must not have a price")
    if kind is OrderKind.LIMIT and (price is None or price <= 0):
        raise OrderFormError("Limit orders require a price > 0")
    return OrderRequest(
        symbol=Symbol(symbol.strip()),
        side=side,
        kind=kind,
        quantity=quantity,
        price=price,
    )

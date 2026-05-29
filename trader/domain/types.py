from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import NewType

from pydantic import BaseModel, ConfigDict

Symbol = NewType("Symbol", str)
OrderId = NewType("OrderId", str)


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderKind(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Position(_Model):
    symbol: Symbol
    quantity: Decimal
    avg_cost: Decimal


class Quote(_Model):
    symbol: Symbol
    bid: Decimal
    ask: Decimal
    last: Decimal


class Order(_Model):
    order_id: OrderId
    symbol: Symbol
    side: Side
    kind: OrderKind
    quantity: Decimal
    price: Decimal | None
    status: OrderStatus
    filled_quantity: Decimal
    avg_fill_price: Decimal | None

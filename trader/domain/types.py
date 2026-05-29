from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import NewType

from pydantic import BaseModel, ConfigDict

Symbol = NewType("Symbol", str)
OrderId = NewType("OrderId", str)


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderKind(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
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

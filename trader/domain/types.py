from __future__ import annotations

from datetime import date, datetime
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


class Portfolio(_Model):
    broker: str
    holdings: dict[Symbol, Decimal]
    drift_tolerance: Decimal


class Fill(_Model):
    symbol: Symbol
    side: Side
    quantity: Decimal
    fill_price: Decimal
    fill_time: datetime
    fees: Decimal
    odno: OrderId


class Trade(_Model):
    symbol: Symbol
    side: Side
    quantity: Decimal
    avg_price: Decimal
    ord_date: date
    ord_time: str
    odno: OrderId


class RealizedPnLRow(_Model):
    symbol: Symbol
    quantity: Decimal
    buy_amount: Decimal
    sell_amount: Decimal
    realized_pnl: Decimal
    return_pct: Decimal


class RealizedPnLSummary(_Model):
    rows: tuple[RealizedPnLRow, ...]
    total_buy_amount: Decimal
    total_sell_amount: Decimal
    total_realized_pnl: Decimal


class UnmatchedSellLeg(_Model):
    symbol: Symbol
    quantity: Decimal
    avg_price: Decimal
    ord_date: date
    ord_time: str
    odno: OrderId


class RealizedPnLReport(_Model):
    rows: tuple[RealizedPnLRow, ...]
    total_buy_amount: Decimal
    total_sell_amount: Decimal
    total_realized_pnl: Decimal
    unmatched_sells: tuple[UnmatchedSellLeg, ...]

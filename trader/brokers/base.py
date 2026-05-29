from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from trader.domain.types import Order, OrderId, OrderKind, Position, Quote, Side, Symbol


class Broker(Protocol):
    """Adapter to a single trading Venue's account API.

    All methods are async. Venue-specific extras live on the concrete class,
    not on this protocol.
    """

    async def get_cash(self) -> Decimal: ...

    async def get_positions(self) -> list[Position]: ...

    async def get_quote(self, symbol: Symbol) -> Quote: ...

    async def place_order(
        self,
        symbol: Symbol,
        side: Side,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None = None,
    ) -> OrderId: ...

    async def get_order(self, order_id: OrderId) -> Order: ...

    async def list_open_orders(self) -> list[Order]: ...

    async def cancel_order(self, order_id: OrderId) -> None: ...

"""Rebalance executor.

Takes a Plan (from `trader.rebalance.plan.plan`) + a Broker + a TokenBucket
rate limiter; submits all non-skipped rows in parallel via asyncio.gather,
polls each placed order to a terminal status, and returns a structured
RebalanceSummary distinguishing filled / rejected / errored outcomes.

No retry on rejection. No auto-rollback. The summary surfaces per-order
detail so the user can act on partial failures manually.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from trader.brokers.base import Broker
from trader.brokers.kis import KISApiError
from trader.domain.types import OrderId, OrderStatus, Side, Symbol
from trader.rebalance.plan import Plan, PlanRow
from trader.rebalance.rate_limiter import TokenBucket

Outcome = Literal["filled", "rejected", "errored"]

_TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


@dataclass(frozen=True)
class OrderOutcome:
    symbol: Symbol
    side: Side
    quantity: Decimal
    outcome: Outcome
    order_id: OrderId | None
    filled_quantity: Decimal
    avg_fill_price: Decimal | None
    reason: str | None


@dataclass(frozen=True)
class RebalanceSummary:
    outcomes: list[OrderOutcome] = field(default_factory=list)

    @property
    def filled(self) -> list[OrderOutcome]:
        return [o for o in self.outcomes if o.outcome == "filled"]

    @property
    def rejected(self) -> list[OrderOutcome]:
        return [o for o in self.outcomes if o.outcome == "rejected"]

    @property
    def errored(self) -> list[OrderOutcome]:
        return [o for o in self.outcomes if o.outcome == "errored"]


async def execute(
    plan: Plan,
    broker: Broker,
    rate_limiter: TokenBucket,
    *,
    poll_max: int = 10,
    poll_interval: float = 1.1,
) -> RebalanceSummary:
    actionable = [r for r in plan.rows if not r.skipped]
    if not actionable:
        return RebalanceSummary()

    coros = [
        _submit_one(r, broker, rate_limiter, poll_max=poll_max, poll_interval=poll_interval)
        for r in actionable
    ]
    outcomes = await asyncio.gather(*coros)
    return RebalanceSummary(outcomes=list(outcomes))


async def _submit_one(
    row: PlanRow,
    broker: Broker,
    rate_limiter: TokenBucket,
    *,
    poll_max: int,
    poll_interval: float,
) -> OrderOutcome:
    qty = row.order_quantity
    await rate_limiter.acquire()
    try:
        order_id = await broker.place_order(row.symbol, row.side, row.kind, qty)
    except KISApiError as e:
        return OrderOutcome(
            symbol=row.symbol,
            side=row.side,
            quantity=qty,
            outcome="rejected",
            order_id=None,
            filled_quantity=Decimal("0"),
            avg_fill_price=None,
            reason=str(e),
        )
    except Exception as e:
        return OrderOutcome(
            symbol=row.symbol,
            side=row.side,
            quantity=qty,
            outcome="errored",
            order_id=None,
            filled_quantity=Decimal("0"),
            avg_fill_price=None,
            reason=f"{type(e).__name__}: {e}",
        )

    last_status = OrderStatus.PENDING
    filled_qty = Decimal("0")
    avg_fill: Decimal | None = None
    for _ in range(poll_max):
        try:
            await rate_limiter.acquire()
            order = await broker.get_order(order_id)
        except KISApiError as e:
            return OrderOutcome(
                symbol=row.symbol,
                side=row.side,
                quantity=qty,
                outcome="errored",
                order_id=order_id,
                filled_quantity=filled_qty,
                avg_fill_price=avg_fill,
                reason=f"poll: {e}",
            )
        last_status = order.status
        filled_qty = order.filled_quantity
        avg_fill = order.avg_fill_price
        if order.status in _TERMINAL:
            break
        await asyncio.sleep(poll_interval)

    if last_status is OrderStatus.FILLED:
        return OrderOutcome(
            symbol=row.symbol,
            side=row.side,
            quantity=qty,
            outcome="filled",
            order_id=order_id,
            filled_quantity=filled_qty,
            avg_fill_price=avg_fill,
            reason=None,
        )
    if last_status is OrderStatus.REJECTED:
        return OrderOutcome(
            symbol=row.symbol,
            side=row.side,
            quantity=qty,
            outcome="rejected",
            order_id=order_id,
            filled_quantity=filled_qty,
            avg_fill_price=avg_fill,
            reason="broker rejected after submit",
        )
    return OrderOutcome(
        symbol=row.symbol,
        side=row.side,
        quantity=qty,
        outcome="errored",
        order_id=order_id,
        filled_quantity=filled_qty,
        avg_fill_price=avg_fill,
        reason=f"still {last_status.value} after {poll_max} polls",
    )

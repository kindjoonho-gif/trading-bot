"""Pure rebalance plan compute.

Given the current account state (positions + cash), a target-weights map from
a Portfolio, current quotes, and a drift tolerance, returns a structured Plan
listing per-Symbol orders + diagnostics. No I/O.

Semantics (see docs/prd/0001-phase-a-kis-rebalance.md):

* Total Value = cash + sum(position.qty * quote.last)
* Per-Symbol target market value = target_weight * Total Value
* Delta shares (raw) = (target MV - current MV) / quote.last
* Rounded toward zero so no order overspends cash or oversells shares
* Skipped if |drift| < tolerance (drift = current_weight - target_weight)
* Skipped if rounded delta shares == 0 (rounds_to_zero)
* All orders are MARKET
* Symbols held but NOT in target_weights are left alone (not in plan)
* Cash Residual = cash - sum(buy proceeds) + sum(sell proceeds) at quoted price
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trader.domain.money import round_toward_zero
from trader.domain.types import OrderKind, Position, Quote, Side, Symbol

SkipReason = Literal["below_tolerance", "rounds_to_zero"] | None


@dataclass(frozen=True)
class PlanRow:
    symbol: Symbol
    current_quantity: Decimal
    current_weight: Decimal
    target_weight: Decimal
    drift: Decimal
    raw_delta_won: Decimal
    raw_delta_shares: Decimal
    rounded_delta_shares: Decimal
    side: Side
    kind: OrderKind
    skipped_reason: SkipReason

    @property
    def skipped(self) -> bool:
        return self.skipped_reason is not None

    @property
    def order_quantity(self) -> Decimal:
        return abs(self.rounded_delta_shares)


@dataclass(frozen=True)
class Plan:
    rows: list[PlanRow]
    total_value: Decimal
    starting_cash: Decimal
    cash_residual: Decimal


def plan(
    positions: list[Position],
    cash: Decimal,
    target_weights: dict[Symbol, Decimal],
    quotes: dict[Symbol, Quote],
    tolerance: Decimal,
) -> Plan:
    pos_by_symbol = {p.symbol: p for p in positions}
    total_value = cash + sum(
        (p.quantity * quotes[p.symbol].last for p in positions),
        start=Decimal("0"),
    )

    rows: list[PlanRow] = []
    for symbol, target_w in target_weights.items():
        price = quotes[symbol].last
        current_qty = pos_by_symbol[symbol].quantity if symbol in pos_by_symbol else Decimal("0")
        current_mv = current_qty * price
        current_w = current_mv / total_value if total_value > 0 else Decimal("0")
        drift = current_w - target_w
        target_mv = target_w * total_value
        raw_delta_won = target_mv - current_mv
        raw_delta_shares = raw_delta_won / price if price > 0 else Decimal("0")
        rounded = round_toward_zero(raw_delta_shares)

        if abs(drift) < tolerance:
            reason: SkipReason = "below_tolerance"
        elif rounded == 0:
            reason = "rounds_to_zero"
        else:
            reason = None

        side = Side.BUY if rounded >= 0 else Side.SELL

        rows.append(
            PlanRow(
                symbol=symbol,
                current_quantity=current_qty,
                current_weight=current_w,
                target_weight=target_w,
                drift=drift,
                raw_delta_won=raw_delta_won,
                raw_delta_shares=raw_delta_shares,
                rounded_delta_shares=rounded,
                side=side,
                kind=OrderKind.MARKET,
                skipped_reason=reason,
            )
        )

    cash_delta = Decimal("0")
    for r in rows:
        if r.skipped or r.rounded_delta_shares == 0:
            continue
        if r.side is Side.BUY:
            cash_delta -= r.order_quantity * quotes[r.symbol].last
        else:
            cash_delta += r.order_quantity * quotes[r.symbol].last
    cash_residual = cash + cash_delta

    return Plan(
        rows=rows,
        total_value=total_value,
        starting_cash=cash,
        cash_residual=cash_residual,
    )

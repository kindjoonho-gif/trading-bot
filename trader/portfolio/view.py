from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trader.domain.types import Position, Quote, Symbol


@dataclass(frozen=True)
class PositionRow:
    symbol: Symbol
    quantity: Decimal
    avg_cost: Decimal
    last: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    weight: Decimal


def build_rows(
    positions: list[Position],
    quotes: dict[Symbol, Quote],
    cash: Decimal,
) -> tuple[list[PositionRow], Decimal]:
    """Enrich positions with quote-derived fields and account weight.

    Total Value = cash + Σ(quantity * last). Weight is share of Total Value.
    """
    market_values = [p.quantity * quotes[p.symbol].last for p in positions]
    total_value = cash + sum(market_values, start=Decimal("0"))
    rows: list[PositionRow] = []
    for pos, mv in zip(positions, market_values, strict=True):
        last = quotes[pos.symbol].last
        weight = mv / total_value if total_value > 0 else Decimal("0")
        rows.append(
            PositionRow(
                symbol=pos.symbol,
                quantity=pos.quantity,
                avg_cost=pos.avg_cost,
                last=last,
                market_value=mv,
                unrealized_pnl=(last - pos.avg_cost) * pos.quantity,
                weight=weight,
            )
        )
    return rows, total_value

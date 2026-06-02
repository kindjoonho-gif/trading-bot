from __future__ import annotations

from collections import defaultdict, deque
from decimal import Decimal

from trader.domain.types import (
    RealizedPnLReport,
    RealizedPnLRow,
    Side,
    Symbol,
    Trade,
    UnmatchedSellLeg,
)

_ZERO = Decimal("0")


def _sort_key(t: Trade) -> tuple[str, str]:
    return (t.ord_date.isoformat(), t.ord_time)


def compute_realized_pnl(trades: list[Trade]) -> RealizedPnLReport:
    """FIFO match sells against prior buys per Symbol, gross of fees.

    Unmatched sell legs (no buy-side basis in `trades`) are surfaced
    separately and excluded from the matched total.
    """
    by_symbol: dict[Symbol, list[Trade]] = defaultdict(list)
    for t in sorted(trades, key=_sort_key):
        by_symbol[t.symbol].append(t)

    rows: list[RealizedPnLRow] = []
    unmatched: list[UnmatchedSellLeg] = []
    total_buy = _ZERO
    total_sell = _ZERO
    total_pnl = _ZERO

    for symbol, sym_trades in by_symbol.items():
        buy_lots: deque[tuple[Decimal, Decimal]] = deque()
        matched_qty = _ZERO
        matched_buy_amt = _ZERO
        matched_sell_amt = _ZERO

        for t in sym_trades:
            if t.side is Side.BUY:
                buy_lots.append((t.quantity, t.avg_price))
                continue

            remaining = t.quantity
            while remaining > _ZERO and buy_lots:
                lot_qty, lot_price = buy_lots[0]
                take = min(remaining, lot_qty)
                matched_qty += take
                matched_buy_amt += take * lot_price
                matched_sell_amt += take * t.avg_price
                remaining -= take
                if take == lot_qty:
                    buy_lots.popleft()
                else:
                    buy_lots[0] = (lot_qty - take, lot_price)

            if remaining > _ZERO:
                unmatched.append(
                    UnmatchedSellLeg(
                        symbol=symbol,
                        quantity=remaining,
                        avg_price=t.avg_price,
                        ord_date=t.ord_date,
                        ord_time=t.ord_time,
                        odno=t.odno,
                    )
                )

        if matched_qty > _ZERO:
            realized = matched_sell_amt - matched_buy_amt
            return_pct = (realized / matched_buy_amt) if matched_buy_amt > _ZERO else _ZERO
            rows.append(
                RealizedPnLRow(
                    symbol=symbol,
                    quantity=matched_qty,
                    buy_amount=matched_buy_amt,
                    sell_amount=matched_sell_amt,
                    realized_pnl=realized,
                    return_pct=return_pct,
                )
            )
            total_buy += matched_buy_amt
            total_sell += matched_sell_amt
            total_pnl += realized

    rows.sort(key=lambda r: r.symbol)
    return RealizedPnLReport(
        rows=tuple(rows),
        total_buy_amount=total_buy,
        total_sell_amount=total_sell,
        total_realized_pnl=total_pnl,
        unmatched_sells=tuple(unmatched),
    )

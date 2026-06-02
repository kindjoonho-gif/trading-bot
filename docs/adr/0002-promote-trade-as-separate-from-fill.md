# Promote `Trade` as separate from `Fill`

**Status:** accepted (2026-06-01)

In Phase A `Fill` was overloaded: the wire-level event (KIS reports "this Order executed Q shares at price P") and the persisted historical record were the same type, even though KIS's `inquire-daily-ccld` only ever returns the aggregated per-Order grain. The CONTEXT.md flagged-ambiguities section already named this gap.

We promote **Trade** to a first-class term: the historical record of a completed Order, one row per (Order, order-date), persisted in the [[History Store]]. **Fill** stays as the wire-level event term — Phase A's `KISBroker.list_fills` keeps its name because that's what the KIS endpoint actually produces (aggregate per Order per day). The Store and its readers speak Trades; the Broker speaks Fills.

## Consequences

- New `trader.domain.types.Trade` pydantic model alongside `Fill`.
- `trader.history.store.HistoryStore` exposes `list_trades`, not `list_fills`.
- The UI History page renders Trades, not Fills.
- Future Brokers (Alpaca, Binance) may report true per-execution Fills; the conversion Fill → Trade happens at Backfill time.

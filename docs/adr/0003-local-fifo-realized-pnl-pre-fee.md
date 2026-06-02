# Local FIFO realized P&L, pre-fee, unmatched excluded

**Status:** accepted (2026-06-01)

`HistoryStore.realized_pnl(start, end)` is computed locally from stored Trades, using FIFO matching of sells against prior buys. Fees and tax are **not** modelled — KIS's `inquire-daily-ccld` does not expose them, so the figure is gross. Any sell whose buy-side cannot be FIFO-matched within the Store's retained history (e.g. shares held from before first Backfill) is excluded from the realized P&L total and surfaced separately as an "unmatched basis" diagnostic.

Chosen over proxying KIS `inquire-period-trade-profit` because (a) that endpoint is real-account-only and returns `EGW02006` on Mock, (b) it is capped to KIS's 3-month window, defeating the [[History Store]]'s entire purpose for P&L, and (c) it forces a live network call for every History view load.

## Consequences

- Our realized P&L number will diverge from any P&L figure KIS or the user's tax report shows, by roughly commissions + transfer tax. This is not a tax tool.
- Users with long-held positions (older than the first Backfill window) will see "unmatched" rows on their first sells. A later slice can let the user annotate a seed cost basis if needed.
- Average-cost matching was considered and rejected; FIFO is more intuitive for "did this trade make money" reporting and is the convention most retail investors expect.

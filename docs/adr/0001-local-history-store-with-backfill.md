# Local History Store with Backfill

**Status:** accepted (2026-06-01)

KIS's `inquire-daily-ccld` retains only today's Trades on the Mock Account and ~3 months on the Live Account, after which the data is gone. We need a record that survives indefinitely so we can track performance, compute realized P&L over arbitrary windows, and (in later phases) feed strategy research.

We keep a local SQLite [[History Store]] (one DB file per Account environment under `data/`), and Backfill it from the Broker on app open and via a daily scheduled job (Windows Task Scheduler, ~16:00 KST after KRX close). Writes are idempotent on `(odno, ord_dt)`. The Streamlit History view reads from the Store, not from the Broker — the Broker's `list_fills` exists only to feed the Backfill.

## Considered alternatives

- **Broker-direct reads (Phase A status quo).** Simplest, but capped by KIS retention and unusable on Mock Account for realized P&L.
- **Write-on-execute only.** Smaller code, but misses Trades placed via HTS/mobile, leaving silent gaps.
- **Long-running daemon for sync.** Avoids the scheduler dependency, but brittle to crashes and wasteful when the app is closed.

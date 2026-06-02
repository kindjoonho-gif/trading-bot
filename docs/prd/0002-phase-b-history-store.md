# PRD 0002 — Phase B: SQLite History Store with KIS Backfill

**Status:** ready-for-agent
**Scope:** Phase B, item 5 of PRD 0001 line 79-83 (own SQLite history store). Other Phase B items (Stop-Loss, WebSocket tape, US/crypto adapters, algo strategies, mixed-Venue baskets) remain out of scope and will get their own PRDs.
**Glossary:** see [CONTEXT.md](../../CONTEXT.md). Notably **Trade**, **History Store**, and **Backfill** are now first-class terms.
**ADRs:** [0001 — Local History Store with Backfill](../adr/0001-local-history-store-with-backfill.md), [0002 — Promote Trade as separate from Fill](../adr/0002-promote-trade-as-separate-from-fill.md), [0003 — Local FIFO realized P&L, pre-fee](../adr/0003-local-fifo-realized-pnl-pre-fee.md).

## Problem Statement

Phase A reads History and realized P&L straight from KIS endpoints. `inquire-daily-ccld` retains only today's Trades on the Mock Account and ~3 months on the Live Account, and `inquire-period-trade-profit` is real-account-only (returns `EGW02006` on Mock) and likewise capped at 3 months. After Phase A's retention window expires, performance history disappears. I want a record that survives indefinitely so I can track multi-year performance, compute realized P&L over arbitrary windows, and (in later phases) feed strategy research from the same substrate.

## Solution

A small local SQLite-backed `HistoryStore` that:

- Persists one [[Trade]] row per `(odno, ord_dt)` pulled from `KISBroker.list_fills`.
- Stores one DB file per Account environment under `data/` (`history_mock.sqlite`, `history_real.sqlite`), selected by `KIS_ENV`. Path overridable via `HISTORY_DB_DIR`. Directory is gitignored.
- Backfills on app open and via a daily Windows Task Scheduler job around 16:00 KST (after KRX close). Steady-state pull window is the last 7 days; on an empty DB the first run pulls the full 3 months (real) or today (mock).
- Writes are idempotent: `INSERT OR IGNORE` keyed on `(odno, ord_dt)`. Re-running the Backfill never duplicates.
- Computes realized P&L locally via FIFO matching of sells against prior buys in the Store, gross of fees. Sells whose buy-side cannot be matched within retained history are excluded from the total and surfaced as a separate diagnostic ("unmatched basis").
- The UI History page reads from the Store, not from the Broker. `KISBroker.list_fills` exists only as the Backfill input. `KISBroker.realized_pnl` is no longer called from the UI but stays on the concrete class for ad-hoc reconciliation.
- Uses `aiosqlite` for async DB access. `journal_mode=WAL` set on every connection. A small retry-on-lock wrapper handles the rare collision between a Streamlit-triggered Backfill and the scheduled job.

## User Stories

1. As a Portfolio manager, I want my completed Trades preserved beyond KIS's 3-month retention, so that I can review multi-year history.
2. As a Portfolio manager, I want my Mock Account Trades preserved separately from Live Account Trades, so that test runs do not contaminate real performance data.
3. As a Portfolio manager, I want Trades placed via HTS or mobile to appear in the Store too, so that my history is complete regardless of how the Order was entered.
4. As a Portfolio manager, I want the History view to load quickly without a fresh KIS round-trip every time, so that I can browse months of activity smoothly.
5. As a Portfolio manager, I want realized P&L computed over any window the Store covers — not just KIS's 3 months — so that I can answer "how did I do last year."
6. As a Portfolio manager, I want realized P&L to work on the Mock Account too, so that I can validate strategies against the same UI before going live.
7. As a Portfolio manager, I want sells whose buy-side cannot be matched against my Store history to be flagged separately, so that I know which P&L numbers are partial and can correct them later.
8. As a developer, I want the Backfill to run on app open without me clicking anything, so that I cannot forget to sync.
9. As a developer, I want a scheduled job (`python -m trader.history.sync`) I can wire to Windows Task Scheduler, so that history is captured even on days I do not open the app.
10. As a developer, I want the Backfill to be safely re-runnable any number of times, so that an interrupted or duplicate run leaves the DB consistent.
11. As a developer, I want the schema versioned with ordered migration scripts, so that the schema can grow without manual data rebuilds.
12. As a developer, I want the History page to keep working unchanged from the user's perspective — same columns, same filters — so that the UI swap is invisible.

## Implementation Decisions

### Architecture

- **Repository pattern.** `trader.history.store.HistoryStore` owns all DB access. It is constructed with a path to one `.sqlite` file and exposes async methods only. The Broker is not a reader for the History page; it remains a Backfill input.
- **Backfill as an orchestration module, not a method on the Store.** `trader.history.sync.run_backfill(broker, store)` does the wiring: pull from Broker via `list_fills`, convert each Fill to a Trade, INSERT-OR-IGNORE into the Store. Keeps Store I/O-pure (DB only).
- **One DB file per environment.** Selected at app start by `KIS_ENV`. No shared cross-env queries.
- **Schema migrations.** Plain SQL files under `trader/history/migrations/000N_*.sql` applied in order by a ~30-LOC runner that maintains a `schema_version` table. First migration creates the `trades` table and the `schema_version` table itself.

### Modules to build (Phase B item 5)

- `trader.domain.types` — add `Trade` pydantic model (frozen, `extra=forbid`): `symbol: Symbol`, `side: Side`, `quantity: Decimal`, `avg_price: Decimal`, `ord_date: date`, `ord_time: str` (HHMMSS), `odno: OrderId`. `Fill` stays as-is.
- `trader.history.store` — `HistoryStore` class. Async methods: `connect()`, `close()`, `apply_migrations()`, `upsert_trades(trades: list[Trade]) -> int` (returns count of new rows), `list_trades(start: date, end: date) -> list[Trade]`, `realized_pnl(start: date, end: date) -> RealizedPnLReport`. Uses aiosqlite; `journal_mode=WAL` set on connect; writes go through a retry-on-lock decorator.
- `trader.history.sync` — `run_backfill(broker, store, *, window_days: int | None = None) -> SyncSummary`. If the Store has zero rows, pulls 90 days (real) or 1 day (mock) on first run; otherwise pulls `window_days` (default 7). Returns counts (`pulled`, `inserted`, `already_present`).
- `trader.history.pnl` — pure module. `compute_realized_pnl(trades: list[Trade]) -> RealizedPnLReport`. FIFO matching of sells against buys, per Symbol, in chronological order. Returns matched-leg total + per-Symbol breakdown + list of unmatched sell legs. No I/O.
- `trader.history.migrations` — migration runner. Scans `trader/history/migrations/*.sql`, applies any whose number exceeds `schema_version`.
- `trader/history/migrations/0001_init.sql` — `CREATE TABLE trades (...)` + `CREATE TABLE schema_version (...)`.
- `trader/history/__main__.py` — CLI entry: `python -m trader.history.sync` does load `.env` → build Broker → build Store → `run_backfill` → print summary. Wires to Windows Task Scheduler.
- `trader.config.settings` — add `HISTORY_DB_DIR: Path = Path("data")`. The actual DB filename is derived as `history_{KIS_ENV}.sqlite`.
- `ui/pages/5_History.py` — change reads from `broker.list_fills` / `broker.realized_pnl` to `store.list_trades` / `store.realized_pnl`. Visually unchanged.

### Key behavioral decisions (from grilling, 2026-06-01)

- **Trade identity:** `(odno, ord_dt)`. INSERT-OR-IGNORE on this pair. Tolerates a multi-day-fill Order showing up across two Backfills by date-stamping on the original order date.
- **Backfill window:** 7 days steady-state, 90 days on first run (real), 1 day on first run (mock). Window is internal; not a user-facing knob in this slice.
- **Mock and real are separate DBs.** No env column on rows. Path resolution looks at `KIS_ENV` at app start and picks the corresponding file.
- **Realized P&L:** FIFO, pre-fee, per Symbol, chronological by `(ord_dt, ord_tmd)`. Unmatched sell legs excluded from total, surfaced separately. See [ADR-0003](../adr/0003-local-fifo-realized-pnl-pre-fee.md).
- **Concurrency:** `journal_mode=WAL` on every connect. Writes wrapped in a 3-attempt exponential-backoff retry on `sqlite3.OperationalError: database is locked`. Reads do not need the retry.
- **Failure handling during Backfill:** any KIS error surfaces in `SyncSummary` and the CLI exits non-zero. Partial Backfill is fine — the next run idempotently fills in.
- **No `Fill` → `Trade` Protocol change.** `Fill` stays on `KISBroker.list_fills` (the wire API). Conversion to `Trade` happens in `run_backfill`. The `Broker` Protocol gets no history methods in this slice.
- **Realized P&L on the Broker:** `KISBroker.realized_pnl` is preserved but unused by the UI. Available for ad-hoc reconciliation against KIS's audited figure.

### Config and tooling

- New dep: `aiosqlite`. Add via `uv add aiosqlite`.
- New env var: `HISTORY_DB_DIR` (default `data`). Add to `.env.example` with a one-line comment.
- `.gitignore` gains `/data/`.
- `tests/unit/` gains coverage for `pnl.py` (FIFO logic, unmatched legs) and `store.py` (round-trip insert + query against an in-memory `:memory:` DB).
- `tests/integration/` gains a KIS_INTEGRATION-gated test that backfills against the Mock Account into a tmp DB and asserts row count > 0 after market hours.

### Windows Task Scheduler setup (one-time)

Documented in a new top-level `SCHEDULE.md`:

```
schtasks /Create /SC DAILY /ST 16:00 /TN "AutotraderHistorySync" ^
  /TR "C:\path\to\python.exe -m trader.history.sync" /F
```

(Or via the GUI — task fires daily at 16:00 KST, runs the sync module, logs to `data/sync.log`.) Detection of "did the schedule run today" is out of scope for this slice; the on-open Backfill is the safety net.

## Testing Decisions

A good test in this codebase exercises an externally observable behavior — the contents of the DB after a Backfill, the realized P&L number for a known Trade tape, the list returned for a date window — never an internal helper or private attribute.

### Modules with required unit tests (deep, pure)

- `trader.history.pnl` — exhaustive: single buy + single sell exact match, partial sell consuming one buy, sell spanning multiple buys (FIFO chained), buy after all sells (unmatched sell preserved), short-side / sell-without-buy (unmatched), buy with no sell (no contribution to realized, position open), multiple Symbols isolated from each other, zero-Trade input.
- `trader.history.store` — round-trip: insert + list-back, dedupe on duplicate `(odno, ord_dt)`, date-window filtering, multi-Symbol query, empty result on cold DB. Use `:memory:` DB.
- `trader.history.migrations` — fresh DB applies all migrations, partially-applied DB applies only the missing ones, `schema_version` table correctly advanced.

### Modules with required integration tests (mostly I/O)

- `trader.history.sync` against the KIS Mock Account, gated by `KIS_INTEGRATION=1` and `_market_only`: empty tmp DB → `run_backfill` → assert `inserted > 0` (assumes prior Mock Account activity from Phase A's I3/I4/I7 tests will appear). Re-run on same DB → assert `inserted == 0`, `already_present == pulled`.

### Modules deliberately not unit-tested

- `ui/pages/5_History.py` — UI shape unchanged from Phase A. Manual smoke only.
- `trader/history/__main__.py` — thin entrypoint. Manual smoke (run from CLI, observe summary line).

## Out of Scope

The following remain deferred to later Phase B PRDs (Phase B items 1-4, 6) or to later phases entirely:

- **Stop-Loss orders.**
- **WebSocket streaming tape** — no live feed in this slice.
- **Algorithmic strategies.**
- **US / crypto adapters.**
- **Mixed-Venue Portfolios** — one DB per env, no cross-Venue aggregation.
- **Per-Fill granularity.** KIS daily-ccld is order-aggregate; we honor that grain. If a future Broker exposes per-execution Fills, the Fill-to-Trade conversion may aggregate; we do not store individual Fills.
- **Fee / tax modelling in P&L.** Pre-fee only — see [ADR-0003](../adr/0003-local-fifo-realized-pnl-pre-fee.md).
- **Backfill of Position snapshots, Quote snapshots, or open Orders.** Trades only — see Q2 of the grilling.
- **Realized P&L reconciliation UI** comparing local FIFO to KIS-reported. Possible future slice.
- **User-provided seed cost basis** for pre-history holdings. Phase B item 5 stops at "unmatched diagnostic."
- **Multi-process write coordination beyond WAL + retry.** No queues, no daemons.
- **Cross-env queries** ("show me both Mock and Real together"). Separate DB files, separate views.

## Further Notes

- **CONTEXT.md is canonical for language.** Trade vs Fill, History Store vs cache — if the PRD seems to drift, the PRD is wrong.
- **KIS daily-ccld pagination** is already implemented in Phase A (`tr_cont` header → next page). The Backfill reuses `KISBroker.list_fills` unchanged; no new pagination logic.
- **Sync log:** the CLI writes a one-line summary (`pulled=N inserted=M already=K`) to stdout. Windows Task Scheduler redirect via `> data/sync.log` is documented in `SCHEDULE.md`. No app-level log framework added.
- **Phase B item 5 is "done" when:** the History page renders Trades from the local Store; realized P&L on the page computes locally and works on both Mock and Real; running `python -m trader.history.sync` twice in a row produces `inserted=0` on the second run; restarting Streamlit shows the previously-synced Trades without a fresh KIS call; on a Mock Account that has prior activity, the History view shows Trades from before today even after KIS would have forgotten them.

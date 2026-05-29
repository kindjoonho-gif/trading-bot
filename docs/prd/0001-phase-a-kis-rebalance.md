# PRD 0001 — Phase A: KIS (KOSPI) manual orders and Portfolio Rebalance

**Status:** ready-for-agent
**Scope:** Phase A only. Phase B work (US equities, crypto, Stop-Loss, WebSocket tape, algo strategies, own DB) is explicitly out of scope.
**Glossary:** see [CONTEXT.md](../../CONTEXT.md). All terms used here are defined there.

## Problem Statement

I trade KOSPI through Korea Investment Securities and I want to (1) place individual buy/sell Orders at either a price I specify or the current best-available price, and (2) hold a Portfolio of multiple Symbols at target percentages and Rebalance the whole basket in one action. Doing this through the broker's HTS by hand is slow, error-prone for baskets, and gives me no way to declare "this is the allocation I want" as a versioned, repeatable artifact. I also know I will eventually want to trade US equities and crypto the same way, so I do not want to build something that has to be rewritten when I add those Venues.

## Solution

A small Python application with a Streamlit UI that:

- Connects to KIS as a Broker, switchable between Mock Account (모의투자) and Live Account via env var at startup.
- Lets me place a Market Order or Limit Order on any KOSPI Symbol, with a preview-and-confirm flow gated by a LIVE Mode toggle that defaults off per session.
- Lets me declare a Portfolio of Target Weights in YAML (using either Korean names or 6-digit Symbols), and produces a Plan listing the exact Orders needed to move my current holdings toward that target.
- Submits the Plan as parallel Orders against KIS, respecting its rate limits, and reports a per-Order success/failure summary.
- Shows my current Positions (with avg cost and unrealized P&L), open Orders (with a Cancel action), and a History of recent Fills with realized P&L — all sourced from KIS endpoints, no separate database.
- Is built around a `Broker` Protocol so that adding `AlpacaBroker` and `BinanceBroker` later is a parallel adapter, not a rewrite of the rebalancing or UI code.

## User Stories

1. As a KOSPI trader, I want to place a Market Order to buy or sell a Symbol, so that I can execute an idea immediately without choosing a price.
2. As a KOSPI trader, I want to place a Limit Order at a price I specify, so that I get price protection on the Fill.
3. As a Portfolio manager, I want to declare my target allocation as a YAML file, so that it is version-controllable and repeatable.
4. As a Portfolio manager, I want to refer to holdings by Korean or English name (e.g. "삼성전자", "Naver"), so that I do not have to memorize 6-digit Symbols.
5. As a Portfolio manager, I want to refer to holdings by 6-digit Symbol when I prefer, so that I can be explicit when a name is ambiguous.
6. As a Portfolio manager, I want the Portfolio loader to fail fast on unknown names, unresolvable duplicates, or Target Weight sums greater than 1.0, so that bad config never reaches the Broker.
7. As a Portfolio manager, I want a Plan rendered before any Orders are sent, so that I can review what will happen.
8. As a Portfolio manager, I want Symbols whose `|Drift| < Drift Tolerance` skipped from the Plan, so that I do not pay transaction costs on noise.
9. As a Portfolio manager, I want share quantities Rounded Toward Zero, so that no Order overspends my cash or oversells my holdings.
10. As a Portfolio manager, I want all Rebalance Orders submitted in parallel, so that the basket fills at similar prices and one Order does not move the market against the next.
11. As a Portfolio manager, I want a per-Order success/failure summary after a Rebalance, so that I can take manual action on any rejection without auto-rollback surprising me.
12. As a developer, I want a `KIS_ENV=mock` switch that points the Broker at the 모의투자 sandbox URL and credentials, so that I can exercise the full Order flow without spending money.
13. As a Portfolio manager, I want a UI-level LIVE Mode toggle that defaults off, so that session start cannot accidentally send a live Order even if the underlying Broker is connected to the Live Account.
14. As a Portfolio manager, when LIVE Mode is off, I want every Order action to behave as a Dry-run regardless of source (single Order page or Rebalance page), so that the toggle is a uniform kill-switch.
15. As a Portfolio manager, I want to see my current Positions with quantity, average cost, current price, and unrealized P&L, so that I know my state before Rebalancing.
16. As a Portfolio manager, I want to see my available cash, so that I can interpret the Cash Residual in the Plan.
17. As a Portfolio manager, I want to see my open (unfilled) Orders with side, type, price, and submission time, so that I can monitor what is sitting in the order book.
18. As a Portfolio manager, I want a Cancel button on each open Order, so that I can withdraw a stale Limit Order without leaving the app.
19. As a Portfolio manager, I want a History view of recent Fills with realized P&L, so that I can track performance within the KIS retention window.
20. As a developer, I want the Broker contract defined as an async Protocol with a small fixed surface (cash, positions, quote, place_order, get_order, list_open_orders, cancel_order) plus history endpoints, so that future Brokers are same-shape adapters.
21. As a developer, I want a Ticker Master that fetches from KRX/KIS and caches to a local CSV, refreshable on demand, so that name resolution does not depend on a live network call per Portfolio load.
22. As a developer, I want money math to use `Decimal` end-to-end (cash, prices, quantities, weights at compute time), so that won totals are exact across compute steps.
23. As a developer, I want strict mypy on the core library, so that `Symbol` vs `OrderId` vs raw `str` mix-ups are caught at edit time.
24. As a developer, I want KIS access tokens cached on disk respecting their 24h TTL, so that I do not hit the token-issue rate limit on restart.
25. As a developer, I want a token-bucket rate limiter wrapping the KIS HTTP client, so that parallel Rebalance submissions stay under the per-second cap.
26. As a Portfolio manager, I want the Drift Tolerance overridable per Portfolio file, so that I can tighten the band for stable allocations and loosen it for high-turnover ones.
27. As a Portfolio manager, I want the Plan compute to be deterministic given a fixed price snapshot, so that the preview matches what gets submitted.
28. As a developer, I want the Plan compute to be a pure function (no I/O), so that it can be unit-tested with synthetic inputs and never silently change behavior based on environment.
29. As a Portfolio manager, I want the Streamlit UI organized as separate pages for Place Order, Rebalance, Positions, Open Orders, and History, so that I can navigate without scrolling.
30. As a developer, I want the UI to be a thin caller of the `trader` package, so that a future CLI or notebook driver can reuse the same core without code duplication.
31. As a developer, I want secrets loaded from `.env` via python-dotenv, with `.env` gitignored and `.env.example` committed, so that onboarding to multi-Venue setups (Alpaca, Binance) keeps the same pattern.

## Implementation Decisions

### Architecture

- **Adapter pattern over a `Broker` Protocol.** One concrete adapter per Venue. Phase A ships `KISBroker`. The Protocol surface is intentionally small (see User Story 20); Venue-specific extras live on the concrete class, not the Protocol.
- **`trader` core library + thin UI layer.** All domain logic (rebalance math, name resolution, Portfolio validation, KIS REST) lives in importable modules under `trader/`. The Streamlit UI is a thin caller. This lets a future CLI or notebook driver reuse the same core.
- **Async core.** Broker methods are `async`. Rebalance submission uses `asyncio.gather` against the Broker, gated by a token-bucket rate limiter sized to KIS limits.

### Modules to build (Phase A)

- `trader.brokers.base` — `Broker` Protocol (already stubbed). Async. Methods: `get_cash`, `get_positions`, `get_quote`, `place_order`, `get_order`, `list_open_orders`, `cancel_order`. History/P&L endpoints to be added as Protocol methods after KIS endpoint inventory is finalized; if Venue-specific they stay on the concrete adapter.
- `trader.brokers.kis` — KIS REST adapter. Includes token cache (24h TTL on disk), `httpx.AsyncClient` with retry via `tenacity`, and token-bucket rate limiter. One module owns auth + all endpoint mappings.
- `trader.domain.types` — `Symbol`, `OrderId` NewTypes; `Side`, `OrderKind`, `OrderStatus` enums; `Position`, `Quote`, `Order` pydantic models (frozen, `extra=forbid`). Already stubbed.
- `trader.domain.money` — `to_decimal`, `round_toward_zero`, `format_krw`. Already stubbed.
- `trader.tickers.master` — fetch KRX/KIS master list, write to local CSV cache. Refreshable on demand. Network I/O only here.
- `trader.tickers.resolver` — pure function: `(master DataFrame, name_or_code) → Symbol`. Raises on unknown; raises on ambiguous name unless caller passed an explicit Symbol.
- `trader.portfolio.loader` — pure transform: YAML dict → validated `Portfolio` model (uses resolver). Enforces `Σ Target Weights ≤ 1.0`, no duplicate Symbols. Default Drift Tolerance 1%, overridable in YAML.
- `trader.rebalance.plan` — **deep pure module.** Signature: `(positions, cash, target_weights, quotes, tolerance) → Plan`. Returns a list of `(Symbol, Side, kind=MARKET, quantity)` entries plus the residual diagnostics (Total Value, per-Symbol current weight, drift, raw Δ shares, rounded Δ shares, skipped-for-tolerance flag). No I/O.
- `trader.rebalance.execute` — orchestrator. Takes a Plan + Broker, submits in parallel via `asyncio.gather`, gathers per-Order results, returns a structured summary (filled / rejected / errored). No retry on rejection — surfaces failure to the UI for the user to act on.
- `trader.config.settings` — `pydantic-settings`-based env loader. Reads `KIS_ENV`, `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`, optional `KIS_BASE_URL`. Selects mock vs real base URL based on `KIS_ENV` if no override.
- `ui/streamlit_app.py` + `ui/pages/1_Place_Order.py` … `5_History.py` — Streamlit multipage app. Shared session state holds the Broker handle and the LIVE Mode toggle (default `False` per session).

### Key behavioral decisions (from grilling)

- **Order types in Phase A:** Market and Limit only. Stop-Loss explicitly Phase B because true server-side stops are limited on KOSPI and client-side stop monitoring brings forward Phase B's monitor-loop infrastructure.
- **Quote source:** REST snapshot only. WebSocket tape deferred to Phase B (where the algo-signal feed will need it anyway).
- **Rebalance Order type:** all Market in Phase A. Limit-at-touch was considered but introduces fill-watch/retry complexity that crosses into Phase B. Single-Order API still supports Limit for ad-hoc manual placement.
- **Rebalance compute model:** Target Weights are fractions of Total Value (`cash + Σ positions × price`). Sum must be `≤ 1.0`; residual is Cash, implicit. Per-Symbol Δ shares rounded toward zero so no Order overspends cash or oversells. Drift Tolerance (default 1% of Total Value) gates whether a Symbol is included in the Plan.
- **Symbol convention:** bare 6-digit string in Phase A. The Portfolio file is bound to one Broker, so the Venue is unambiguous. Promoting to a `Symbol(market, code)` type is a trivial future migration when mixed-Venue baskets land.
- **Mock vs Live:** `KIS_ENV` selects at startup. Independent of the UI LIVE Mode toggle, which is a per-session runtime kill-switch defaulting off. Both must be permissive for an Order to actually reach the Broker.
- **No own database in Phase A.** Positions, open Orders, Fill history, and realized P&L all come from KIS endpoints. Phase A history is bounded by KIS retention (months, not years). A SQLite-backed history store is explicit Phase B work.
- **Failure handling:** parallel submission, no automatic rollback. Per-Order failure surfaced in the UI summary. The user decides whether to manually offset.

### Config and tooling

- Python 3.12+. Deps managed by `uv`. Lint+format `ruff`. Types `mypy --strict` on `trader/`. Tests `pytest` with `pytest-asyncio` in auto mode. KIS REST mocked in unit tests via `respx`.
- Secrets in `.env` (gitignored). `.env.example` committed. KIS access token cached to a local file in `.cache/`.

### Portfolio YAML shape

```yaml
broker: KIS
holdings:
  "005930": 0.30     # or "삼성전자"
  "000660": 0.20
  "035420": 0.10
drift_tolerance: 0.01     # optional; default 0.01
```

## Testing Decisions

A good test in this codebase exercises an externally observable behavior — the result of a Plan compute, the structure of a parsed Portfolio, the resolution of a name, the JSON body the KIS client sends — never an internal helper or private attribute. Tests should fail when the user-visible behavior breaks, and only then.

### Modules with required unit tests (deep, pure)

- `trader.rebalance.plan` — exhaustive cases: exact-fit allocation, residual cash, Symbol below Drift Tolerance skipped, sell side rounded toward zero, buy side rounded toward zero, single-Symbol Portfolio, all-cash Portfolio, sum-of-weights == 1.0, target weight = 0 for an existing Position (full liquidation), price unchanged from a prior compute is idempotent.
- `trader.tickers.resolver` — Korean name → Symbol, English name → Symbol, raw 6-digit pass-through, unknown raises, ambiguous name raises with both candidates surfaced, whitespace and case tolerance.
- `trader.portfolio.loader` — valid file, sum > 1.0 raises, duplicate Symbols raise, names resolved, Drift Tolerance override respected, missing optional fields default correctly.
- `trader.domain.money` — `round_toward_zero` on positive, negative, exact-integer, fractional-just-below-one, very-small-fraction inputs.

### Modules with required integration tests (mostly I/O)

- `trader.brokers.kis` against the KIS Mock Account (모의투자): auth handshake + token cache, each endpoint round-trip (cash, positions, quote on a real KOSPI Symbol e.g. 005930, place + get + cancel Limit Order, place Market Order, list_open_orders), error-shape mapping (rejection → typed exception).
- `trader.rebalance.execute` end-to-end: load example Portfolio → compute Plan → submit against Mock Account → verify parallel submission stays under rate limit → verify failure summary on a forced rejection (e.g. nonsensical price).

### Modules deliberately not unit-tested in Phase A

- `ui/*` (Streamlit) — covered by manual smoke. Streamlit testing is high-friction and the UI is a thin caller; bugs surface immediately on use.
- `trader.tickers.master` — network fetch + CSV write. Smoke test only (run, check file exists, check row count > 0).

### Prior art

This is a new repo; there is no prior test corpus to mirror. Test layout: `tests/unit/` for pure-module tests, `tests/integration/` for KIS Mock Account tests guarded by a `KIS_INTEGRATION=1` env gate so they do not run by default in CI without credentials.

## Out of Scope

The following are deliberately **not** part of Phase A and have been deferred to Phase B (or later):

- **Stop-Loss orders** (any form — server-side conditional, client-side monitor, or otherwise).
- **WebSocket streaming** — no live tape, no continuous price feed. REST snapshot only.
- **Algorithmic strategies** — no signal generation, no auto-trading. Every Order is user-initiated.
- **US equities and cryptocurrency Venues.** The `Broker` Protocol is designed to accommodate them; adapters are not built in Phase A.
- **Mixed-Venue Portfolios.** One Portfolio file = one Broker = one Venue.
- **Own persistent database.** Fill history and realized P&L come from KIS endpoints only, subject to KIS retention.
- **Order modification ("amend") and replace-after-cancel chaining.** Phase A supports place and cancel only.
- **TWAP, VWAP, iceberg, post-only, IOC, FOK** or any non-Market/Limit order type.
- **Pre-market / after-hours sessions** — KIS pre-market endpoints exist but are not wired up in Phase A.
- **Per-asset Limit override in Rebalance** (Q13 option C). Rebalance is all Market in Phase A.
- **Mobile or non-localhost web deploy.** Streamlit runs locally only.
- **Multi-user / authentication.** Single-user, single-machine app.

## Further Notes

- **KIS API specifics to handle in `brokers.kis`:** access token has a 24-hour TTL (cache to disk under `.cache/`); `approval_key` for WebSocket is a separate flow (not needed in Phase A); rate limit ≈ 20 req/s on real, ≈ 2 req/s on mock (verify per endpoint at implementation time); `CANO` and `ACNT_PRDT_CD` are split from the configured account number.
- **KOSPI specifics:** lot size is 1 share for nearly all names, so Round Toward Zero on share quantity is exact and simple. No fractional shares allowed. Sell-side tax ≈ 0.2% + commission ≈ 0.015% — informs the 1% Drift Tolerance default.
- **Money:** KRW is integer-valued (no sub-won). `Decimal` is used to keep cross-Venue patterns consistent (USD has cents, crypto has 8 decimals).
- **History endpoints:** KIS exposes filled-order history and a realized-P&L endpoint (`손익`). Phase A wraps both; the History page paginates by date over whatever window KIS retains.
- **CONTEXT.md is canonical for language.** If a term in this PRD seems off, the PRD is wrong, not the glossary.
- **Phase A is "done" when:** Streamlit app launches, a Place Order page sends one Market + one Limit Order against Mock Account that fills and shows up in Positions; the example Portfolio Rebalances against Mock Account, the Plan matches the post-execution Positions within rounding, History shows the resulting Fills, and a live-flip with LIVE Mode off correctly Dry-runs.

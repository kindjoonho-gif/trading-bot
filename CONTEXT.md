# Autotrader

Automated portfolio rebalancing and manual order placement against a brokerage account. Phase A covers KOSPI via Korea Investment Securities (KIS). Later phases add US equities and crypto.

## Language

### Trading entities

**Broker**:
A connector to a single trading **Venue**'s account API. One Broker is bound to one Portfolio at a time.
_Avoid_: gateway, client, adapter (these refer to the implementation; the concept is Broker).

**Venue**:
The marketplace itself (KOSPI, NYSE, Binance). The Broker connects *to* a Venue.
_Avoid_: exchange (overloaded — also means crypto exchange platform), market (overloaded — also means "Market Order").

**Symbol**:
The Venue-native string that identifies a tradeable asset. KOSPI uses 6-digit codes; US uses letter tickers; crypto uses pair strings. Each Symbol belongs to exactly one Venue.
_Avoid_: ticker (ambiguous — see "Ticker Master"), code, instrument.

**Ticker Master**:
A Venue-published list mapping human-readable names (e.g. "삼성전자") to Symbols (e.g. "005930"). Used to resolve Portfolio entries written in either form.

### Orders and execution

**Order**:
An instruction to buy or sell a specified Quantity of a Symbol. An Order has a Side and a type (Market or Limit). An Order becomes one or more Fills when executed.
_Avoid_: trade (Trade = post-execution record), purchase, transaction.

**Side**:
The direction of an Order — `buy` or `sell`.

**Market Order**:
An Order with no price; fills at the best available counterparty price immediately.

**Limit Order**:
An Order with a price ceiling (buy) or floor (sell); waits in the order book until a matching counterparty appears or the Order is Cancelled.

**Quote**:
A snapshot of the current bid, ask, and last-traded price for one Symbol at one instant. Snapshots are point-in-time — a fresh Quote is required for each compute.

**Fill**:
A confirmation from the Broker that an Order executed at a specific price and quantity. An Order may produce one Fill (full) or several (partial).

**Cancel**:
An instruction to withdraw an unfilled or partially-filled Order from the Venue's order book.
_Avoid_: revoke, withdraw, kill.

### Portfolio and rebalancing

**Position**:
A current holding — Symbol, quantity owned, and average cost basis.
_Avoid_: holding (alias).

**Portfolio**:
A target basket: a set of Symbols with desired Target Weights, plus an implicit Cash Residual. A Portfolio is bound to one Broker (and hence one Venue) in Phase A.

**Target Weight**:
The fraction of Total Value a Symbol should occupy. Range `[0, 1]`. Sum across the Portfolio must satisfy `≤ 1.0`; the leftover becomes the Cash Residual.

**Total Value**:
`cash + Σ(Position.quantity × current Quote price)`. The denominator for all weight math.

**Cash Residual**:
`1 − Σ(Target Weights)`. The implicit fraction of Total Value left as cash, not held in any Symbol.

**Drift**:
`current_weight − target_weight` for one Symbol.

**Drift Tolerance**:
Minimum `|Drift|` that triggers a Rebalance Order. Below this threshold the Symbol is skipped. Default `1%`.

**Rebalance**:
The action of computing the per-Symbol Order list needed to bring the current Portfolio toward Target Weights, then optionally submitting it.

**Plan**:
The proposed Order list produced by the Rebalance compute step. Always displayed before any Order is sent (the Dry-run output).

**Round Toward Zero**:
Share-rounding convention for the Plan: `floor(|Δ shares|)` preserving Side. Guarantees no Order exceeds available cash (on buys) or available shares (on sells).

### Safety and environments

**Mock Account** (모의투자):
A KIS-provided sandbox using fake money against real-time market data. Same API surface as the Live Account; different credentials and base URL.
_Avoid_: paper trading (correct in US/crypto contexts; KIS uses 모의투자), sandbox (overloaded with general dev sandboxes).

**Live Account**:
The real-money brokerage account.

**LIVE Mode**:
A UI runtime toggle that, when **off**, forces Dry-run regardless of which Account the Broker is connected to. Default off per UI session. Independent of Mock/Live Account selection (which is a startup-time config).

**Dry-run**:
A Rebalance or single Order action that computes and displays the Plan but does not submit anything to the Broker.

## Flagged ambiguities

- **"Market"** appears in two unrelated senses: the **Venue** (e.g. "the KOSPI market") and the **Market Order** type. Always qualify in writing — "KOSPI Venue" or "Market Order" — never bare "market."
- **"Order"** vs **"Trade"** vs **"Fill"**: Order = pre-execution intent. Fill = the Broker event confirming partial or full execution. Trade = historical record of a completed Order (sum of its Fills). Phase A does not surface Trade as a distinct concept; the History view shows Fills grouped by Order.
- **"Cancel"**: refers only to user-initiated withdrawal of an open Order. Broker-side rejections (insufficient cash, market closed, etc.) are *Rejections*, not Cancels.

## Example dialogue

> **Dev:** I added LG Energy at 20% to the Portfolio. After Rebalance, I see no Order for it.
> **Domain:** Drift was below Tolerance. Current weight was 19.3%; `|Drift| = 0.7%` — under the 1% threshold. The Plan skipped it.
> **Dev:** OK. And Naver shows a smaller Quantity than I'd expect from `0.10 × Total Value / price`.
> **Domain:** Naver's Δ was 12.7 shares. Rounded Toward Zero to 12. The 0.7 share remainder stays in Cash Residual — that's intentional, so the buy never overshoots available cash.
> **Dev:** Got it. If I flip LIVE Mode off and hit Execute, what happens?
> **Domain:** Dry-run. The Plan renders but no Order goes to the Broker. The LIVE toggle is independent of whether you're on the Mock Account or Live Account — even a Mock connection respects it.

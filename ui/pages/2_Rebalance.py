from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError
from trader.domain.money import format_krw
from trader.domain.types import Position, Quote, Symbol
from trader.portfolio.loader import PortfolioLoadError
from trader.portfolio.loader import load as load_portfolio
from trader.rebalance.plan import Plan, plan
from ui._common import make_broker, render_sidebar, run_async

st.set_page_config(page_title="Rebalance · Autotrader", layout="wide")
render_sidebar()

st.title("Rebalance")
st.caption("Plan only — execution lands in I7 (#9).")

default_path = str(Path("portfolios/example.yaml").resolve())
yaml_path = st.text_input("Portfolio YAML path", value=default_path)

_RATE_LIMIT_SLEEP = 1.1


async def _fetch(
    symbols: set[Symbol],
) -> tuple[Decimal, list[Position], dict[Symbol, Quote]]:
    async with make_broker() as b:
        cash = await b.get_cash()
        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        positions = await b.get_positions()
        needed = symbols | {p.symbol for p in positions}
        quotes: dict[Symbol, Quote] = {}
        for s in needed:
            await asyncio.sleep(_RATE_LIMIT_SLEEP)
            quotes[s] = await b.get_quote(s)
        return cash, positions, quotes


if not yaml_path.strip():
    st.info("Enter a portfolio YAML path to plan a rebalance.")
    st.stop()

try:
    portfolio = load_portfolio(yaml_path)
except PortfolioLoadError as e:
    st.error(f"Portfolio load failed: {e}")
    st.stop()
except FileNotFoundError:
    st.error(f"File not found: {yaml_path}")
    st.stop()

st.subheader("Portfolio")
st.write(f"Broker: `{portfolio.broker}` · Drift tolerance: `{portfolio.drift_tolerance}`")
st.dataframe(
    pd.DataFrame(
        [{"Symbol": s, "Target weight": float(w)} for s, w in portfolio.holdings.items()]
    ),
    hide_index=True,
)

try:
    with st.spinner("Fetching cash, positions, and quotes (serialized for rate limit)..."):
        cash, positions, quotes = run_async(_fetch(set(portfolio.holdings)))
except (KISAuthError, KISApiError) as e:
    st.error(f"KIS error: {e}")
    st.stop()
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")
    st.stop()

result: Plan = plan(positions, cash, portfolio.holdings, quotes, portfolio.drift_tolerance)

st.subheader("Diagnostics")
c1, c2, c3 = st.columns(3)
c1.metric("Total value", format_krw(result.total_value))
c2.metric("Starting cash", format_krw(result.starting_cash))
c3.metric("Cash residual (post-plan)", format_krw(result.cash_residual))

st.subheader("Plan")
if not result.rows:
    st.info("No rows in plan (empty portfolio).")
else:
    df = pd.DataFrame(
        [
            {
                "Symbol": r.symbol,
                "Action": "—"
                if r.skipped
                else ("BUY" if r.side.value == "buy" else "SELL"),
                "Qty (shares)": int(r.order_quantity) if not r.skipped else 0,
                "Current weight (%)": round(float(r.current_weight * 100), 3),
                "Target weight (%)": round(float(r.target_weight * 100), 3),
                "Drift (%)": round(float(r.drift * 100), 3),
                "Raw Δ shares": round(float(r.raw_delta_shares), 3),
                "Rounded Δ shares": int(r.rounded_delta_shares),
                "Skipped": "" if not r.skipped else (r.skipped_reason or "skipped"),
            }
            for r in result.rows
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)

    skipped_rows = [r for r in result.rows if r.skipped]
    actionable_rows = [r for r in result.rows if not r.skipped]
    if actionable_rows:
        buys = [r for r in actionable_rows if r.side.value == "buy"]
        sells = [r for r in actionable_rows if r.side.value == "sell"]
        st.write(f"**Actionable:** {len(buys)} buy · {len(sells)} sell")
    if skipped_rows:
        parts = [
            f"{r.symbol} (drift {r.drift:.3%}, {r.skipped_reason})" for r in skipped_rows
        ]
        st.write(f"**Skipped ({len(skipped_rows)}):** " + ", ".join(parts))

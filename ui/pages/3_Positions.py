from __future__ import annotations

import asyncio
from decimal import Decimal

import pandas as pd
import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError
from trader.domain.money import format_krw
from trader.domain.types import Position, Quote, Symbol
from trader.portfolio.view import build_rows
from ui._common import make_broker, render_sidebar, run_async

st.set_page_config(page_title="Positions · Autotrader", layout="wide")
render_sidebar()

st.title("Positions")

_RATE_LIMIT_SLEEP = 1.1


async def _fetch() -> tuple[Decimal, list[Position], dict[Symbol, Quote]]:
    async with make_broker() as b:
        cash = await b.get_cash()
        await asyncio.sleep(_RATE_LIMIT_SLEEP)
        positions = await b.get_positions()
        quotes: dict[Symbol, Quote] = {}
        for p in positions:
            await asyncio.sleep(_RATE_LIMIT_SLEEP)
            quotes[p.symbol] = await b.get_quote(p.symbol)
        return cash, positions, quotes


try:
    cash, positions, quotes = run_async(_fetch())
except (KISAuthError, KISApiError) as e:
    st.error(f"KIS error: {e}")
    st.stop()
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")
    st.stop()

rows, total_value = build_rows(positions, quotes, cash)

col_cash, col_total = st.columns(2)
col_cash.metric("Available cash", format_krw(cash))
col_total.metric("Total value", format_krw(total_value))

st.subheader("Holdings")
if not rows:
    st.info("No open positions.")
else:
    df = pd.DataFrame(
        [
            {
                "Symbol": r.symbol,
                "Qty": float(r.quantity),
                "Avg cost": float(r.avg_cost),
                "Last": float(r.last),
                "Unrealized P&L (₩)": float(r.unrealized_pnl),
                "Weight (%)": float(r.weight * 100),
            }
            for r in rows
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Avg cost": st.column_config.NumberColumn(format="%.2f"),
            "Last": st.column_config.NumberColumn(format="%.2f"),
            "Unrealized P&L (₩)": st.column_config.NumberColumn(format="%,.0f"),
            "Weight (%)": st.column_config.NumberColumn(format="%.2f"),
        },
    )

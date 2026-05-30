from __future__ import annotations

import asyncio
from decimal import Decimal

import pandas as pd
import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError, KISBroker
from trader.domain.money import format_krw
from trader.domain.types import Position, Quote, Symbol
from trader.portfolio.view import build_rows
from ui._common import get_cached_broker, render_sidebar, run_async

st.set_page_config(page_title="Positions · Autotrader", layout="wide")
render_sidebar()

st.title("Positions")

broker = get_cached_broker()


async def _fetch(b: KISBroker) -> tuple[Decimal, list[Position], dict[Symbol, Quote]]:
    cash, positions = await asyncio.gather(b.get_cash(), b.get_positions())
    if not positions:
        return cash, [], {}
    quote_list = await asyncio.gather(*(b.get_quote(p.symbol) for p in positions))
    return cash, positions, {q.symbol: q for q in quote_list}


try:
    cash, positions, quotes = run_async(_fetch(broker))
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

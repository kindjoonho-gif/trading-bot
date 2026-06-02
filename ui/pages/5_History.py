from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from trader.domain.money import format_krw
from trader.domain.types import RealizedPnLReport, Trade
from ui._common import bootstrap_backfill, make_store, render_sidebar, run_async

st.set_page_config(page_title="History · Autotrader", layout="wide")
render_sidebar()
bootstrap_backfill()

st.title("History")

today = date.today()
default_start = today - timedelta(days=30)

c1, c2 = st.columns(2)
start = c1.date_input("Start date", value=default_start, max_value=today)
end = c2.date_input("End date", value=today, max_value=today)

if start > end:
    st.error("Start date must be on or before end date.")
    st.stop()


async def _fetch_trades(s: date, e: date) -> list[Trade]:
    store = make_store()
    await store.connect()
    try:
        return await store.list_trades(s, e)
    finally:
        await store.close()


async def _fetch_pnl(s: date, e: date) -> RealizedPnLReport:
    store = make_store()
    await store.connect()
    try:
        return await store.realized_pnl(s, e)
    finally:
        await store.close()


st.subheader("Trades")
try:
    with st.spinner(f"Loading trades {start} → {end}..."):
        trades = run_async(_fetch_trades(start, end))
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")
    trades = []

if not trades:
    st.info("No trades in this window. The local history store backfills on app open.")
else:
    df = pd.DataFrame(
        [
            {
                "Date": t.ord_date.isoformat(),
                "Time": f"{t.ord_time[:2]}:{t.ord_time[2:4]}:{t.ord_time[4:6]}",
                "Symbol": t.symbol,
                "Side": t.side.value,
                "Qty": int(t.quantity),
                "Avg price ₩": format_krw(t.avg_price),
                "Order ID": t.odno,
            }
            for t in trades
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)

st.subheader("Realized P&L (local FIFO, pre-fee)")
try:
    with st.spinner(f"Computing realized P&L {start} → {end}..."):
        pnl = run_async(_fetch_pnl(start, end))
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")
    pnl = None

if pnl is not None:
    if not pnl.rows:
        st.info("No matched realized P&L rows in this window.")
    else:
        df_pnl = pd.DataFrame(
            [
                {
                    "Symbol": r.symbol,
                    "Qty matched": int(r.quantity),
                    "Buy amt ₩": format_krw(r.buy_amount),
                    "Sell amt ₩": format_krw(r.sell_amount),
                    "Realized P&L ₩": format_krw(r.realized_pnl),
                    "Return %": float(r.return_pct),
                }
                for r in pnl.rows
            ]
        )
        st.dataframe(df_pnl, hide_index=True, use_container_width=True)
    col_buy, col_sell, col_pnl = st.columns(3)
    col_buy.metric("Total buy", format_krw(pnl.total_buy_amount))
    col_sell.metric("Total sell", format_krw(pnl.total_sell_amount))
    col_pnl.metric("Grand total P&L", format_krw(pnl.total_realized_pnl))

    if pnl.unmatched_sells:
        st.warning(
            f"{len(pnl.unmatched_sells)} sell leg(s) had no buy-side basis in the "
            "retained history and are excluded from the matched total."
        )
        df_unmatched = pd.DataFrame(
            [
                {
                    "Date": leg.ord_date.isoformat(),
                    "Symbol": leg.symbol,
                    "Qty unmatched": int(leg.quantity),
                    "Sell price ₩": format_krw(leg.avg_price),
                    "Order ID": leg.odno,
                }
                for leg in pnl.unmatched_sells
            ]
        )
        st.dataframe(df_unmatched, hide_index=True, use_container_width=True)

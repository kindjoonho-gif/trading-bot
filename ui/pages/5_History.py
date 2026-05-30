from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError
from trader.domain.money import format_krw
from trader.domain.types import Fill, RealizedPnLSummary
from ui._common import make_broker, render_sidebar, run_async

st.set_page_config(page_title="History · Autotrader", layout="wide")
render_sidebar()

st.title("History")

today = date.today()
default_start = today - timedelta(days=30)

c1, c2 = st.columns(2)
start = c1.date_input("Start date", value=default_start, max_value=today)
end = c2.date_input("End date", value=today, max_value=today)

if start > end:
    st.error("Start date must be on or before end date.")
    st.stop()


async def _fetch_fills(s: date, e: date) -> list[Fill]:
    async with make_broker() as b:
        return await b.list_fills(s, e)


async def _fetch_pnl(s: date, e: date) -> RealizedPnLSummary:
    async with make_broker() as b:
        return await b.realized_pnl(s, e)


st.subheader("Fills")
try:
    with st.spinner(f"Fetching fills {start} → {end}..."):
        fills = run_async(_fetch_fills(start, end))
except (KISAuthError, KISApiError) as e:
    st.error(f"KIS error fetching fills: {e}")
    fills = []
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")
    fills = []

if not fills:
    st.info("No fills in this window.")
else:
    df = pd.DataFrame(
        [
            {
                "Time": f.fill_time.strftime("%Y-%m-%d %H:%M:%S"),
                "Symbol": f.symbol,
                "Side": f.side.value,
                "Qty": int(f.quantity),
                "Fill price ₩": format_krw(f.fill_price),
                "Fees ₩": format_krw(f.fees),
            }
            for f in fills
        ]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.caption(
        "Fees column is always ₩0 — KIS inquire-daily-ccld does not surface "
        "per-fill commission or sell-side tax."
    )

st.subheader("Realized P&L")
try:
    with st.spinner(f"Fetching realized P&L {start} → {end}..."):
        pnl = run_async(_fetch_pnl(start, end))
except KISApiError as e:
    st.warning(
        f"Realized P&L unavailable for this account: {e}. "
        "KIS mock typically doesn't support TR `TTTC8715R`; this works on a real account."
    )
    pnl = None
except KISAuthError as e:
    st.error(f"KIS auth error: {e}")
    pnl = None
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")
    pnl = None

if pnl is not None:
    if not pnl.rows:
        st.info("No realized P&L rows in this window.")
    else:
        df_pnl = pd.DataFrame(
            [
                {
                    "Symbol": r.symbol,
                    "Qty": int(r.quantity),
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

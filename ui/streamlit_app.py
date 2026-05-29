from __future__ import annotations

import streamlit as st

from ui._common import render_sidebar

st.set_page_config(page_title="Autotrader", layout="wide")
render_sidebar()

st.title("Autotrader")
st.markdown(
    """
Phase A — KIS (KOSPI) Broker.

Pick a page on the left.

- **Place Order** — single Market or Limit order (lands in slice I3).
- **Rebalance** — Portfolio target-weight rebalance (slice I6 / I7).
- **Positions** — current holdings and cash. Cash live now.
- **Open Orders** — unfilled orders with cancel (slice I4).
- **History** — fills and realized P&L (slice I8).

`LIVE Mode` in the sidebar is the runtime kill-switch. When off, every order action is a Dry-run.
"""
)

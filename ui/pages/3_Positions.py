from __future__ import annotations

import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError
from trader.domain.money import format_krw
from ui._common import get_cached_broker, render_sidebar, run_async

st.set_page_config(page_title="Positions · Autotrader", layout="wide")
render_sidebar()

st.title("Positions")

broker = get_cached_broker()

st.subheader("Cash")
try:
    cash = run_async(broker.get_cash())
    st.metric("Available cash", format_krw(cash))
except (KISAuthError, KISApiError) as e:
    st.error(f"KIS error: {e}")
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")

st.subheader("Holdings")
st.info("Holdings + Quote live in I2 (issue #4).")

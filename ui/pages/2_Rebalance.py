from __future__ import annotations

import streamlit as st

from ui._common import render_sidebar

st.set_page_config(page_title="Rebalance · Autotrader", layout="wide")
render_sidebar()

st.title("Rebalance")
st.info("Not implemented in this slice. Plan compute lands in I6 (#8); execute lands in I7 (#9).")

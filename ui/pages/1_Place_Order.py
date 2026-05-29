from __future__ import annotations

import streamlit as st

from ui._common import render_sidebar

st.set_page_config(page_title="Place Order · Autotrader", layout="wide")
render_sidebar()

st.title("Place Order")
st.info("Not implemented in this slice. Lands in I3 (issue #5).")

from __future__ import annotations

import streamlit as st

from ui._common import render_sidebar

st.set_page_config(page_title="History · Autotrader", layout="wide")
render_sidebar()

st.title("History")
st.info("Not implemented in this slice. Lands in I8 (issue #6).")

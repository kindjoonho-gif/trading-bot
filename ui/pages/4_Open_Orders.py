from __future__ import annotations

import streamlit as st

from ui._common import render_sidebar

st.set_page_config(page_title="Open Orders · Autotrader", layout="wide")
render_sidebar()

st.title("Open Orders")
st.info("Not implemented in this slice. Lands in I4 (issue #7).")

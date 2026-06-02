from __future__ import annotations

import streamlit as st

from trader.tickers import master
from ui._common import render_sidebar

st.set_page_config(page_title="Tickers · Autotrader", layout="wide")
render_sidebar()

st.title("KOSPI Tickers")
st.caption(
    "Search the KIS-published KOSPI master by 6-digit code, Korean name, "
    "or English name. Copy a `symbol` value into `portfolios/*.yaml` or the "
    "Place Order page."
)


@st.cache_data(show_spinner=False)
def _load_master() -> tuple[object, str]:
    df = master.load()
    return df, f"{len(df)} rows"


col_refresh, col_status = st.columns([1, 5])
if col_refresh.button("Refresh master", help="Re-download kospi_code.mst from KIS"):
    _load_master.clear()
    with st.spinner("Downloading + parsing kospi_code.mst..."):
        path = master.refresh()
    st.success(f"Wrote {path}")

try:
    df, status = _load_master()
except master.TickerMasterError as e:
    st.error(
        f"{e}. Click **Refresh master** to download the latest KOSPI master, "
        "or run `uv run python -m trader.tickers.master refresh` from the CLI."
    )
    st.stop()

col_status.caption(status)

query = st.text_input(
    "Search",
    placeholder="삼성전자 / Samsung / 005930",
    help="Matches by symbol prefix or substring of Korean/English name (case-insensitive).",
)

if query.strip():
    q = query.strip().casefold()
    mask = (
        df["symbol"].astype(str).str.startswith(q)
        | df["name_ko"].fillna("").astype(str).str.casefold().str.contains(q, regex=False)
        | df["name_en"].fillna("").astype(str).str.casefold().str.contains(q, regex=False)
    )
    hits = df.loc[mask, ["symbol", "name_ko", "name_en"]]
else:
    hits = df[["symbol", "name_ko", "name_en"]].head(50)
    st.caption(f"Showing first 50 of {len(df)} rows. Type to search.")

st.dataframe(
    hits,
    hide_index=True,
    use_container_width=True,
    column_config={
        "symbol": st.column_config.TextColumn("Symbol", width="small"),
        "name_ko": st.column_config.TextColumn("Korean name"),
        "name_en": st.column_config.TextColumn("English name"),
    },
)

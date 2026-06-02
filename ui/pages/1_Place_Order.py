from __future__ import annotations

import asyncio
from decimal import Decimal

import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError
from trader.domain.types import (
    Exchange,
    Order,
    OrderId,
    OrderKind,
    OrderStatus,
    Quote,
    Side,
    Symbol,
)
from trader.orders.form import OrderFormError, OrderRequest, validate_order_form
from trader.tickers import master
from ui._common import current_exchange, is_live, make_broker, render_sidebar, run_async

st.set_page_config(page_title="Place Order · Autotrader", layout="wide")
render_sidebar()

st.title("Place Order")

_SYMBOL_INPUT_KEY = "place_order_symbol"
_PRICE_VALUE_KEY = "place_order_price"
if _SYMBOL_INPUT_KEY not in st.session_state:
    st.session_state[_SYMBOL_INPUT_KEY] = "005930"
if _PRICE_VALUE_KEY not in st.session_state:
    st.session_state[_PRICE_VALUE_KEY] = 0


async def _fetch_quote(sym: str) -> Quote:
    async with make_broker() as b:
        return await b.get_quote(Symbol(sym))


@st.cache_data(show_spinner=False)
def _load_master_safe() -> object | None:
    try:
        return master.load()
    except master.TickerMasterError:
        return None


with st.expander("🔎 Find a KOSPI symbol", expanded=False):
    df = _load_master_safe()
    if df is None:
        st.info(
            "No KOSPI master cached. Open the Tickers page and click "
            "**Refresh master**, then come back."
        )
    else:
        q = st.text_input(
            "Search",
            placeholder="삼성전자 / Samsung / 005930",
            key="po_lookup_query",
        ).strip()
        if q:
            qf = q.casefold()
            mask = (
                df["symbol"].astype(str).str.startswith(q)
                | df["name_ko"].fillna("").astype(str).str.casefold().str.contains(qf, regex=False)
                | df["name_en"].fillna("").astype(str).str.casefold().str.contains(qf, regex=False)
            )
            hits = df.loc[mask, ["symbol", "name_ko", "name_en"]].head(15)
            if hits.empty:
                st.caption("No matches.")
            else:
                for row in hits.itertuples(index=False):
                    cols = st.columns([1, 3, 3, 1])
                    cols[0].code(row.symbol)
                    cols[1].write(row.name_ko)
                    cols[2].write(row.name_en or "—")
                    if cols[3].button("Use", key=f"po_use_{row.symbol}"):
                        st.session_state[_SYMBOL_INPUT_KEY] = row.symbol
                        st.rerun()
        else:
            st.caption("Type a symbol, Korean name, or English name to search.")

_POLL_INTERVAL_S = 1.1
_POLL_MAX = 10
_TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


def _envelope_table(req: OrderRequest) -> dict[str, str]:
    return {
        "Symbol": req.symbol,
        "Side": req.side.value,
        "Kind": req.kind.value,
        "Quantity": str(req.quantity),
        "Price": "(market)" if req.price is None else f"{req.price:,}",
    }


async def _place_and_poll(
    req: OrderRequest, exchange: Exchange
) -> tuple[OrderId, Order | None]:
    async with make_broker(exchange=exchange) as b:
        oid = await b.place_order(req.symbol, req.side, req.kind, req.quantity, req.price)
        last: Order | None = None
        for _ in range(_POLL_MAX):
            try:
                last = await b.get_order(oid)
            except KISApiError:
                last = None
            if last is not None and last.status in _TERMINAL:
                return oid, last
            await asyncio.sleep(_POLL_INTERVAL_S)
        return oid, last


sidebar_ex = current_exchange()
exchanges: list[Exchange] = ["KRX", "NXT", "SOR"]
exchange_in: Exchange = st.selectbox(
    "Exchange",
    options=exchanges,
    index=exchanges.index(sidebar_ex),
    help=f"Defaults to sidebar selection ({sidebar_ex}). Override for one-off routing.",
)
if exchange_in != "KRX":
    st.info(
        "NXT after-market sessions accept **LIMIT only** (KIS code `APBK3013` on "
        "Market). Use LIMIT during pre/after-hours."
    )

_QUOTE_SNAPSHOT_KEY = "place_order_quote_snapshot"
symbol_in = st.text_input(
    "Symbol",
    key=_SYMBOL_INPUT_KEY,
    help="6-digit KOSPI code. Use the 🔎 search above to fill from the master.",
)
_current_sym = symbol_in or "005930"
if st.button(
    f"📡 Fetch quote for {_current_sym}",
    help="Reads bid / ask / last via KIS get_quote. Click a price below to fill.",
):
    try:
        with st.spinner("Fetching quote..."):
            q = run_async(_fetch_quote(_current_sym))
        st.session_state[_QUOTE_SNAPSHOT_KEY] = {
            "symbol": _current_sym,
            "bid": int(q.bid),
            "ask": int(q.ask),
            "last": int(q.last),
        }
        st.rerun()
    except (KISAuthError, KISApiError) as e:
        st.error(f"Quote fetch failed: {e}")

_snapshot = st.session_state.get(_QUOTE_SNAPSHOT_KEY)
if _snapshot and _snapshot["symbol"] == _current_sym:
    bid_col, ask_col, last_col = st.columns(3)
    if bid_col.button(f"Bid ₩{_snapshot['bid']:,}", key="po_use_bid"):
        st.session_state[_PRICE_VALUE_KEY] = _snapshot["bid"]
        st.rerun()
    if ask_col.button(f"Ask ₩{_snapshot['ask']:,}", key="po_use_ask"):
        st.session_state[_PRICE_VALUE_KEY] = _snapshot["ask"]
        st.rerun()
    if last_col.button(f"Last ₩{_snapshot['last']:,}", key="po_use_last"):
        st.session_state[_PRICE_VALUE_KEY] = _snapshot["last"]
        st.rerun()
    st.caption(
        "NXT after-market price bands are tighter than KRX (±~10% vs ±30%). "
        "Prefer Bid (buy) or Ask (sell) to stay safely inside the band."
    )

with st.form("place_order_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    side_in = c1.selectbox("Side", options=[Side.BUY, Side.SELL], format_func=lambda s: s.value)
    if exchange_in == "KRX":
        kind_options = [OrderKind.MARKET, OrderKind.LIMIT]
    else:
        kind_options = [OrderKind.LIMIT, OrderKind.MARKET]  # LIMIT first for NXT/SOR
    kind_in = c2.selectbox(
        "Kind", options=kind_options, format_func=lambda k: k.value
    )
    c3, c4 = st.columns(2)
    qty_in = c3.number_input("Quantity", min_value=1, value=1, step=1)
    price_in = c4.number_input(
        "Price (Limit only)",
        min_value=0,
        value=st.session_state[_PRICE_VALUE_KEY],
        step=100,
        help="Use the 📡 button above to fill from the latest quote.",
    )
    submitted = st.form_submit_button("Submit", type="primary")

if submitted:
    try:
        price = Decimal(str(price_in)) if kind_in is OrderKind.LIMIT and price_in > 0 else None
        req = validate_order_form(symbol_in, side_in, kind_in, Decimal(str(qty_in)), price)
    except OrderFormError as e:
        st.error(f"Invalid order: {e}")
        st.stop()

    st.session_state["pending_req"] = req
    st.session_state["pending_exchange"] = exchange_in

req: OrderRequest | None = st.session_state.get("pending_req")
pending_exchange: Exchange = st.session_state.get("pending_exchange", current_exchange())

if req is not None:
    st.subheader("Order envelope")
    envelope = _envelope_table(req) | {"Exchange": pending_exchange}
    st.table(envelope)

    if not is_live():
        st.warning("Dry-run — not sent. Enable LIVE Mode in the sidebar to submit for real.")
        st.session_state.pop("pending_req", None)
    else:
        st.error("LIVE Mode is ON. Submitting will place a real order via KIS.")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("Confirm submit", type="primary", key="confirm_submit"):
            try:
                with st.spinner("Placing order + polling status..."):
                    oid, order = run_async(_place_and_poll(req, pending_exchange))
            except (KISAuthError, KISApiError) as e:
                st.error(f"KIS error: {e}")
                st.session_state.pop("pending_req", None)
                st.stop()
            st.success(f"Order placed. OrderId: `{oid}`")
            if order is None:
                st.info(
                    "Order accepted but status not yet visible in today's orders. "
                    "Check History page."
                )
            elif order.status in _TERMINAL:
                st.write(f"**Terminal status:** {order.status.value}")
                st.write(f"Filled: {order.filled_quantity} / {order.quantity}")
                if order.avg_fill_price is not None:
                    st.write(f"Avg fill price: ₩{order.avg_fill_price:,}")
            else:
                st.info(
                    f"Still {order.status.value} after {_POLL_MAX} polls. "
                    "Filled so far: "
                    f"{order.filled_quantity} / {order.quantity}. Check History page."
                )
            st.session_state.pop("pending_req", None)
        if cancel_col.button("Cancel", key="cancel_submit"):
            st.session_state.pop("pending_req", None)
            st.rerun()

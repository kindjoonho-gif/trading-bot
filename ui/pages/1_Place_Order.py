from __future__ import annotations

import asyncio
from decimal import Decimal

import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError
from trader.domain.types import Order, OrderId, OrderKind, OrderStatus, Side
from trader.orders.form import OrderFormError, OrderRequest, validate_order_form
from ui._common import is_live, make_broker, render_sidebar, run_async

st.set_page_config(page_title="Place Order · Autotrader", layout="wide")
render_sidebar()

st.title("Place Order")

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


async def _place_and_poll(req: OrderRequest) -> tuple[OrderId, Order | None]:
    async with make_broker() as b:
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


with st.form("place_order_form", clear_on_submit=False):
    c1, c2, c3 = st.columns(3)
    symbol_in = c1.text_input("Symbol", value="005930", help="6-digit KOSPI code")
    side_in = c2.selectbox("Side", options=[Side.BUY, Side.SELL], format_func=lambda s: s.value)
    kind_in = c3.selectbox(
        "Kind", options=[OrderKind.MARKET, OrderKind.LIMIT], format_func=lambda k: k.value
    )
    c4, c5 = st.columns(2)
    qty_in = c4.number_input("Quantity", min_value=1, value=1, step=1)
    price_in = c5.number_input("Price (Limit only)", min_value=0, value=0, step=100)
    submitted = st.form_submit_button("Submit", type="primary")

if submitted:
    try:
        price = Decimal(str(price_in)) if kind_in is OrderKind.LIMIT and price_in > 0 else None
        req = validate_order_form(symbol_in, side_in, kind_in, Decimal(str(qty_in)), price)
    except OrderFormError as e:
        st.error(f"Invalid order: {e}")
        st.stop()

    st.session_state["pending_req"] = req

req: OrderRequest | None = st.session_state.get("pending_req")

if req is not None:
    st.subheader("Order envelope")
    st.table(_envelope_table(req))

    if not is_live():
        st.warning("Dry-run — not sent. Enable LIVE Mode in the sidebar to submit for real.")
        st.session_state.pop("pending_req", None)
    else:
        st.error("LIVE Mode is ON. Submitting will place a real order via KIS.")
        confirm_col, cancel_col = st.columns(2)
        if confirm_col.button("Confirm submit", type="primary", key="confirm_submit"):
            try:
                with st.spinner("Placing order + polling status..."):
                    oid, order = run_async(_place_and_poll(req))
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

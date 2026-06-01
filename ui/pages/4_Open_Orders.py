from __future__ import annotations

import pandas as pd
import streamlit as st

from trader.brokers.kis import KISApiError, KISAuthError
from trader.domain.money import format_krw
from trader.domain.types import Order, OrderId
from ui._common import is_live, make_broker, render_sidebar, run_async

st.set_page_config(page_title="Open Orders · Autotrader", layout="wide")
render_sidebar()

st.title("Open Orders")
st.caption("Today's unfilled orders (PENDING + PARTIAL). Multi-day stale orders not shown.")

if st.button("Refresh", key="refresh_open"):
    st.rerun()


async def _list_open() -> list[Order]:
    async with make_broker() as b:
        return await b.list_open_orders()


async def _cancel(order_id: OrderId) -> None:
    async with make_broker() as b:
        await b.cancel_order(order_id)


try:
    with st.spinner("Fetching open orders..."):
        orders = run_async(_list_open())
except (KISAuthError, KISApiError) as e:
    st.error(f"KIS error: {e}")
    st.stop()
except Exception as e:
    st.error(f"Unexpected error: {type(e).__name__}: {e}")
    st.stop()

if not orders:
    st.info("No open orders today.")
    st.stop()

df = pd.DataFrame(
    [
        {
            "OrderId": o.order_id,
            "Symbol": o.symbol,
            "Side": o.side.value,
            "Kind": o.kind.value,
            "Qty": int(o.quantity),
            "Price": format_krw(o.price) if o.price is not None else "(market)",
            "Filled": int(o.filled_quantity),
            "Status": o.status.value,
        }
        for o in orders
    ]
)
st.dataframe(df, hide_index=True, use_container_width=True)

st.subheader("Cancel")

for order in orders:
    cols = st.columns([3, 1])
    cols[0].write(
        f"`{order.order_id}` — {order.symbol} {order.side.value} "
        f"{int(order.quantity)} @ {format_krw(order.price) if order.price else '(market)'}"
    )
    cancel_key = f"cancel_{order.order_id}"
    if cols[1].button("Cancel", key=cancel_key):
        if not is_live():
            st.warning(
                f"Dry-run — cancel for `{order.order_id}` NOT sent. "
                "Enable LIVE Mode in the sidebar to cancel for real."
            )
        else:
            try:
                with st.spinner(f"Canceling {order.order_id}..."):
                    run_async(_cancel(order.order_id))
                st.success(f"Cancel submitted for `{order.order_id}`. Refresh to confirm.")
            except (KISAuthError, KISApiError) as e:
                st.error(f"Cancel failed for `{order.order_id}`: {e}")
            except Exception as e:
                st.error(f"Unexpected error: {type(e).__name__}: {e}")

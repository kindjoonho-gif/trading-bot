from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

import streamlit as st

from trader.brokers.kis import KISBroker
from trader.config.settings import Settings, get_settings
from trader.history.store import HistoryStore
from trader.history.sync import run_backfill

T = TypeVar("T")

LIVE_MODE_KEY = "live_mode"
BACKFILL_DONE_KEY = "_backfill_done"


def init_session() -> None:
    """Seed session_state defaults once per session."""
    if LIVE_MODE_KEY not in st.session_state:
        st.session_state[LIVE_MODE_KEY] = False


def render_sidebar() -> None:
    init_session()
    with st.sidebar:
        st.markdown("### Safety")
        st.toggle(
            "LIVE Mode",
            key=LIVE_MODE_KEY,
            help="When off, all order actions are Dry-run regardless of KIS_ENV.",
        )
        s = get_settings()
        st.caption(f"`KIS_ENV={s.KIS_ENV}` · base `{s.base_url}`")


def is_live() -> bool:
    return bool(st.session_state.get(LIVE_MODE_KEY, False))


@st.cache_resource(show_spinner=False)
def get_cached_settings() -> Settings:
    return get_settings()


def make_broker() -> KISBroker:
    """Construct a fresh KISBroker (owning its own httpx.AsyncClient).

    Not cached across reruns: a cached httpx client's asyncio primitives bind
    to its creation-time event loop, but Streamlit reruns spawn fresh loops
    via run_async, causing "bound to a different event loop" errors.
    Always use inside `async with` so the client is closed cleanly.
    """
    return KISBroker(get_cached_settings())


def make_store() -> HistoryStore:
    """Fresh HistoryStore per call. Same event-loop reasoning as make_broker.
    Always use inside `try/finally` with `await store.close()`.
    """
    s = get_cached_settings()
    s.history_db_path.parent.mkdir(parents=True, exist_ok=True)
    return HistoryStore(s.history_db_path)


def run_async[T](coro: Awaitable[T]) -> T:
    """Run an async coroutine from Streamlit's sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError("event loop already running")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


async def _bootstrap() -> tuple[int, int]:
    store = make_store()
    await store.connect()
    await store.apply_migrations()
    broker = make_broker()
    try:
        summary = await run_backfill(broker, store, env=get_cached_settings().KIS_ENV)
    finally:
        await broker.aclose()
        await store.close()
    return summary.pulled, summary.inserted


def bootstrap_backfill() -> None:
    """Once per session, pull recent Fills from KIS into the local Store.

    Best-effort: any error is shown as a non-fatal toast so the History page
    still renders whatever the Store already has.
    """
    if st.session_state.get(BACKFILL_DONE_KEY):
        return
    try:
        pulled, inserted = run_async(_bootstrap())
        if pulled > 0:
            st.toast(f"Backfill: pulled {pulled}, inserted {inserted}", icon="✅")
    except Exception as e:
        st.toast(f"Backfill skipped: {type(e).__name__}", icon="⚠️")
    finally:
        st.session_state[BACKFILL_DONE_KEY] = True

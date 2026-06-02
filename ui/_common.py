from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

import streamlit as st

from trader.brokers.kis import KISBroker
from trader.config.settings import Settings, get_settings, reset_settings_cache
from trader.domain.types import Exchange
from trader.history.store import HistoryStore
from trader.history.sync import run_backfill

T = TypeVar("T")

LIVE_MODE_KEY = "live_mode"
KIS_ENV_KEY = "kis_env"
EXCHANGE_KEY = "exchange"
BACKFILL_DONE_KEY = "_backfill_done"

_EXCHANGES: list[Exchange] = ["KRX", "NXT", "SOR"]


def init_session() -> None:
    """Seed session_state defaults once per session.

    A browser refresh starts a new Streamlit session but keeps the same Python
    process, which means `get_settings()`'s module cache still holds the .env
    values read at boot. Reset that cache on the first init of each session so
    a manual .env edit takes effect on refresh.
    """
    if KIS_ENV_KEY not in st.session_state:
        reset_settings_cache()
        st.session_state[KIS_ENV_KEY] = get_settings().KIS_ENV
    if LIVE_MODE_KEY not in st.session_state:
        st.session_state[LIVE_MODE_KEY] = False
    if EXCHANGE_KEY not in st.session_state:
        st.session_state[EXCHANGE_KEY] = "KRX"


def render_sidebar() -> None:
    """Render the persistent sidebar widgets.

    Streamlit multipage quirk: widget state bound by `key=` is dropped when the
    user navigates to a different page, even though `st.session_state` survives.
    Workaround: don't pass `key=`. Drive each widget via `value=`/`index=` from
    persistent storage and write the return value back. Streamlit then has no
    widget key to garbage-collect; we own the only state.
    """
    init_session()
    with st.sidebar:
        st.markdown("### Account")
        current_env = st.session_state[KIS_ENV_KEY]
        new_env = st.radio(
            "KIS environment",
            options=["mock", "real"],
            index=0 if current_env == "mock" else 1,
            horizontal=True,
            help="Switches the active KIS account at runtime. Each env has "
            "its own token cache and history DB.",
        )
        if new_env != current_env:
            st.session_state[KIS_ENV_KEY] = new_env
            # Force re-bootstrap of backfill against the new env's DB.
            st.session_state[BACKFILL_DONE_KEY] = False
            # Drop the LIVE flag on env switch as a safety re-arm.
            st.session_state[LIVE_MODE_KEY] = False
            st.rerun()
        st.markdown("### Exchange")
        current_ex = st.session_state[EXCHANGE_KEY]
        new_ex = st.radio(
            "Order routing",
            options=_EXCHANGES,
            index=_EXCHANGES.index(current_ex),
            horizontal=True,
            help=(
                "KRX = regular Korea Exchange (정규장 hours). "
                "NXT = NEXTRADE alt venue (pre/after-hours). "
                "SOR = Smart Order Routing (KIS picks best venue)."
            ),
        )
        if new_ex != current_ex:
            st.session_state[EXCHANGE_KEY] = new_ex
            st.rerun()
        st.markdown("### Safety")
        st.session_state[LIVE_MODE_KEY] = st.toggle(
            "LIVE Mode",
            value=st.session_state[LIVE_MODE_KEY],
            help="When off, all order actions are Dry-run regardless of KIS_ENV.",
        )
        s = get_cached_settings()
        st.caption(
            f"`KIS_ENV={s.KIS_ENV}` · exchange `{st.session_state[EXCHANGE_KEY]}` · "
            f"base `{s.base_url}`"
        )


def is_live() -> bool:
    return bool(st.session_state.get(LIVE_MODE_KEY, False))


def get_cached_settings() -> Settings:
    """Settings honoring any sidebar-selected KIS_ENV override.

    Not actually cached: pydantic-settings' `Settings()` re-reads .env per call
    but allows constructor kwargs to override individual fields. Calling this
    per-rerun is fine — the UI makes few calls.
    """
    env_override = st.session_state.get(KIS_ENV_KEY)
    if env_override and env_override != get_settings().KIS_ENV:
        return Settings(KIS_ENV=env_override)  # type: ignore[call-arg]
    return get_settings()


def current_exchange() -> Exchange:
    return st.session_state.get(EXCHANGE_KEY, "KRX")


def make_broker(exchange: Exchange | None = None) -> KISBroker:
    """Construct a fresh KISBroker (owning its own httpx.AsyncClient).

    Not cached across reruns: a cached httpx client's asyncio primitives bind
    to its creation-time event loop, but Streamlit reruns spawn fresh loops
    via run_async, causing "bound to a different event loop" errors.
    Always use inside `async with` so the client is closed cleanly.

    `exchange` defaults to the sidebar-selected routing; pass a value to
    override for a single order (e.g. force KRX during a session set to NXT).
    """
    return KISBroker(get_cached_settings(), exchange=exchange or current_exchange())


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

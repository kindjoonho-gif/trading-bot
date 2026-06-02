from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from trader.domain.types import Fill, OrderId, Trade
from trader.history.store import HistoryStore

KIS_ENV = Literal["mock", "real"]

_KST = timezone(timedelta(hours=9))
_STEADY_WINDOW_DAYS = 7
_FIRST_RUN_MOCK_DAYS = 1
_FIRST_RUN_REAL_DAYS = 90


class FillSource(Protocol):
    async def list_fills(self, start_date: date, end_date: date) -> list[Fill]: ...


class SyncSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    pulled: int
    inserted: int
    already_present: int
    start_date: date
    end_date: date


def _today_kst() -> date:
    return datetime.now(_KST).date()


def _resolve_window(
    *,
    env: KIS_ENV,
    store_empty: bool,
    window_days: int | None,
) -> int:
    if window_days is not None:
        return window_days
    if store_empty:
        return _FIRST_RUN_MOCK_DAYS if env == "mock" else _FIRST_RUN_REAL_DAYS
    return _STEADY_WINDOW_DAYS


def _fill_to_trade(fill: Fill) -> Trade:
    return Trade(
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        avg_price=fill.fill_price,
        ord_date=fill.fill_time.date(),
        ord_time=fill.fill_time.strftime("%H%M%S"),
        odno=OrderId(str(fill.odno)),
    )


async def run_backfill(
    broker: FillSource,
    store: HistoryStore,
    *,
    env: KIS_ENV,
    window_days: int | None = None,
    today: date | None = None,
) -> SyncSummary:
    """Pull Fills from `broker`, convert to Trades, idempotently insert into `store`.

    Window selection:
      - explicit `window_days` overrides everything;
      - empty Store: 1 day on mock, 90 days on real;
      - non-empty Store: 7 days.
    """
    end = today if today is not None else _today_kst()
    store_empty = (await store.count_trades()) == 0
    window = _resolve_window(env=env, store_empty=store_empty, window_days=window_days)
    start = end - timedelta(days=window - 1)

    fills = await broker.list_fills(start, end)
    trades = [_fill_to_trade(f) for f in fills]
    inserted = await store.upsert_trades(trades)
    return SyncSummary(
        pulled=len(fills),
        inserted=inserted,
        already_present=len(fills) - inserted,
        start_date=start,
        end_date=end,
    )

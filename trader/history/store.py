from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from functools import wraps
from pathlib import Path
from typing import Any

import aiosqlite

from trader.domain.types import OrderId, RealizedPnLReport, Side, Symbol, Trade
from trader.history.migrations import apply_migrations
from trader.history.pnl import compute_realized_pnl

_LOCK_RETRIES = 3
_LOCK_BASE_SLEEP_S = 0.05


def _retry_on_lock[T](fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        last: Exception | None = None
        for attempt in range(_LOCK_RETRIES):
            try:
                return await fn(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                last = exc
                await asyncio.sleep(_LOCK_BASE_SLEEP_S * (2**attempt))
        assert last is not None
        raise last

    return wrapper


class HistoryStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        conn = await aiosqlite.connect(self._path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        if self._conn is None:
            return
        await self._conn.close()
        self._conn = None

    async def apply_migrations(self) -> int:
        conn = self._require_conn()
        return await apply_migrations(conn)

    @_retry_on_lock
    async def upsert_trades(self, trades: list[Trade]) -> int:
        if not trades:
            return 0
        conn = self._require_conn()
        before = await self._row_count()
        rows = [
            (
                str(t.odno),
                t.ord_date.isoformat(),
                str(t.symbol),
                t.side.value,
                str(t.quantity),
                str(t.avg_price),
                t.ord_time,
            )
            for t in trades
        ]
        await conn.executemany(
            "INSERT OR IGNORE INTO trades "
            "(odno, ord_date, symbol, side, quantity, avg_price, ord_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await conn.commit()
        after = await self._row_count()
        return after - before

    async def list_trades(self, start: date, end: date) -> list[Trade]:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT odno, ord_date, symbol, side, quantity, avg_price, ord_time "
            "FROM trades WHERE ord_date >= ? AND ord_date <= ? "
            "ORDER BY ord_date, ord_time, odno",
            (start.isoformat(), end.isoformat()),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Trade(
                odno=OrderId(str(r[0])),
                ord_date=date.fromisoformat(str(r[1])),
                symbol=Symbol(str(r[2])),
                side=Side(str(r[3])),
                quantity=Decimal(str(r[4])),
                avg_price=Decimal(str(r[5])),
                ord_time=str(r[6]),
            )
            for r in rows
        ]

    async def count_trades(self) -> int:
        return await self._row_count()

    async def realized_pnl(self, start: date, end: date) -> RealizedPnLReport:
        return compute_realized_pnl(await self.list_trades(start, end))

    async def _row_count(self) -> int:
        conn = self._require_conn()
        async with conn.execute("SELECT COUNT(*) FROM trades") as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("HistoryStore not connected; call connect() first")
        return self._conn

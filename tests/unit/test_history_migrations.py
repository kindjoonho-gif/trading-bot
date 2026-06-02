from __future__ import annotations

import aiosqlite

from trader.history.migrations import apply_migrations


async def _read_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row else 0


class TestApplyMigrations:
    async def test_fresh_db_creates_schema_and_tables(self) -> None:
        conn = await aiosqlite.connect(":memory:")
        try:
            applied = await apply_migrations(conn)
            assert applied == 1
            async with conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ) as cur:
                tables = [str(r[0]) for r in await cur.fetchall()]
            assert "trades" in tables
            assert "schema_version" in tables
            assert await _read_version(conn) == 1
        finally:
            await conn.close()

    async def test_idempotent_on_already_applied(self) -> None:
        conn = await aiosqlite.connect(":memory:")
        try:
            await apply_migrations(conn)
            applied_again = await apply_migrations(conn)
            assert applied_again == 0
            assert await _read_version(conn) == 1
        finally:
            await conn.close()

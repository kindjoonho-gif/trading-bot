from __future__ import annotations

import re
from pathlib import Path

import aiosqlite

_MIGRATION_RE = re.compile(r"^(\d{4})_.*\.sql$")
_DIR = Path(__file__).parent


def _discover() -> list[tuple[int, Path]]:
    items: list[tuple[int, Path]] = []
    for path in _DIR.iterdir():
        if not path.is_file():
            continue
        m = _MIGRATION_RE.match(path.name)
        if m is None:
            continue
        items.append((int(m.group(1)), path))
    items.sort(key=lambda x: x[0])
    return items


async def apply_migrations(conn: aiosqlite.Connection) -> int:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY,"
        "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    await conn.commit()
    async with conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version") as cur:
        row = await cur.fetchone()
    current = int(row[0]) if row else 0
    applied = 0
    for version, path in _discover():
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        await conn.executescript(sql)
        await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        await conn.commit()
        applied += 1
    return applied

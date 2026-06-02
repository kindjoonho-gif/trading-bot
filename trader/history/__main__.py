from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from trader.brokers.kis import KISBroker
from trader.config.settings import get_settings
from trader.history.store import HistoryStore
from trader.history.sync import run_backfill


def _resolve_db_path(env: str) -> Path:
    base = Path(os.environ.get("HISTORY_DB_DIR", "data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / f"history_{env}.sqlite"


async def _amain() -> int:
    settings = get_settings()
    settings.require_credentials()
    db_path = _resolve_db_path(settings.KIS_ENV)
    store = HistoryStore(db_path)
    await store.connect()
    await store.apply_migrations()
    broker = KISBroker(settings)
    try:
        summary = await run_backfill(broker, store, env=settings.KIS_ENV)
    finally:
        await broker.aclose()
        await store.close()
    print(
        f"pulled={summary.pulled} "
        f"inserted={summary.inserted} "
        f"already={summary.already_present} "
        f"window={summary.start_date.isoformat()}..{summary.end_date.isoformat()} "
        f"db={db_path}"
    )
    return 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()

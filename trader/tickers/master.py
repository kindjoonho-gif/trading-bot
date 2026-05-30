"""KOSPI Ticker Master fetch + cache.

Downloads the KIS-published ``kospi_code.mst`` (fixed-width, cp949), parses
``symbol`` and ``name_ko``, merges English names from a small bundled aux CSV,
and writes ``.cache/tickers_master_kis_<YYYYMMDD>.csv``.

CLI:
    uv run python -m trader.tickers.master refresh
"""

from __future__ import annotations

import io
import sys
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

KOSPI_MST_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
DEFAULT_CACHE_DIR = Path(".cache")
ENGLISH_AUX_PATH = Path(__file__).with_name("english_aux.csv")

CACHE_PREFIX = "tickers_master_kis_"

# Byte slices into each MST line (cp949). Layout per KIS-published spec.
_SYMBOL_SLICE = slice(0, 9)
_NAME_KO_SLICE = slice(21, 61)


class TickerMasterError(RuntimeError):
    pass


def _parse_mst(content: bytes) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw in content.splitlines():
        if len(raw) < 61:
            continue
        symbol = raw[_SYMBOL_SLICE].decode("ascii", errors="ignore").strip()
        if len(symbol) != 6 or not symbol.isdigit():
            continue
        name_ko = raw[_NAME_KO_SLICE].decode("cp949", errors="ignore").strip()
        if not name_ko:
            continue
        rows.append((symbol, name_ko))
    return rows


def _download_mst() -> bytes:
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        resp = client.get(KOSPI_MST_URL)
        resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        mst_names = [n for n in zf.namelist() if n.lower().endswith(".mst")]
        if not mst_names:
            raise TickerMasterError(f"no .mst entry in zip: {zf.namelist()}")
        with zf.open(mst_names[0]) as fh:
            return fh.read()


def refresh(cache_dir: Path = DEFAULT_CACHE_DIR, today: date | None = None) -> Path:
    today = today or date.today()
    content = _download_mst()
    rows = _parse_mst(content)
    if not rows:
        raise TickerMasterError("parsed 0 rows from mst file")
    df = pd.DataFrame(rows, columns=["symbol", "name_ko"])
    eng = pd.read_csv(ENGLISH_AUX_PATH, dtype={"symbol": str})
    df = df.merge(eng, on="symbol", how="left")
    df["name_en"] = df["name_en"].fillna("")
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{CACHE_PREFIX}{today:%Y%m%d}.csv"
    df.to_csv(out, index=False, encoding="utf-8")
    return out


def load(cache_dir: Path = DEFAULT_CACHE_DIR) -> pd.DataFrame:
    """Read the newest cached master CSV. Raises if no cache exists."""
    candidates = sorted(cache_dir.glob(f"{CACHE_PREFIX}*.csv"))
    if not candidates:
        raise TickerMasterError(f"no master CSV in {cache_dir}; run `refresh` first")
    df = pd.read_csv(candidates[-1], dtype={"symbol": str})
    df["name_en"] = df["name_en"].fillna("")
    return df


def _main(argv: list[str]) -> int:
    if argv == ["refresh"]:
        path = refresh()
        df = load()
        print(f"wrote {path} ({len(df)} rows)")
        return 0
    print("usage: python -m trader.tickers.master refresh", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

"""Load a Portfolio YAML into a validated ``Portfolio`` model.

YAML shape::

    broker: KIS
    holdings:
      "005930": 0.30     # or "삼성전자"
      "000660": 0.20
    drift_tolerance: 0.01    # optional; default 0.01
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from trader.domain.money import to_decimal
from trader.domain.types import Portfolio, Symbol
from trader.tickers import master as ticker_master
from trader.tickers.resolver import resolve

DEFAULT_DRIFT_TOLERANCE = Decimal("0.01")


class PortfolioLoadError(ValueError):
    pass


def load(path: str | Path, *, master_df: pd.DataFrame | None = None) -> Portfolio:
    raw_text = Path(path).read_text(encoding="utf-8")
    parsed: Any = yaml.safe_load(raw_text)
    if not isinstance(parsed, dict):
        raise PortfolioLoadError(
            f"{path}: top-level must be a mapping, got {type(parsed).__name__}"
        )

    broker = parsed.get("broker")
    if not isinstance(broker, str) or not broker:
        raise PortfolioLoadError(f"{path}: 'broker' must be a non-empty string")

    holdings_raw = parsed.get("holdings", {})
    if not isinstance(holdings_raw, dict):
        raise PortfolioLoadError(f"{path}: 'holdings' must be a mapping")

    drift_raw = parsed.get("drift_tolerance", DEFAULT_DRIFT_TOLERANCE)
    try:
        drift = to_decimal(drift_raw)
    except (TypeError, ValueError) as e:
        raise PortfolioLoadError(f"{path}: invalid drift_tolerance {drift_raw!r}") from e
    if drift < 0 or drift > 1:
        raise PortfolioLoadError(f"{path}: drift_tolerance {drift} not in [0, 1]")

    if master_df is None:
        master_df = ticker_master.load()

    holdings: dict[Symbol, Decimal] = {}
    for key, weight_raw in holdings_raw.items():
        symbol = resolve(master_df, str(key))
        if symbol in holdings:
            raise PortfolioLoadError(
                f"{path}: duplicate symbol after resolution: {symbol} (from {key!r})"
            )
        try:
            weight = to_decimal(weight_raw)
        except (TypeError, ValueError) as e:
            raise PortfolioLoadError(
                f"{path}: invalid weight for {key!r}: {weight_raw!r}"
            ) from e
        if weight < 0 or weight > 1:
            raise PortfolioLoadError(f"{path}: weight for {key!r} not in [0, 1]: {weight}")
        holdings[symbol] = weight

    total = sum(holdings.values(), start=Decimal("0"))
    if total > Decimal("1"):
        raise PortfolioLoadError(f"{path}: sum of weights > 1.0: {total}")

    return Portfolio(broker=broker, holdings=holdings, drift_tolerance=drift)

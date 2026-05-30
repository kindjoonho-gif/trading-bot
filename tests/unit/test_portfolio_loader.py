from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from trader.domain.types import Symbol
from trader.portfolio.loader import DEFAULT_DRIFT_TOLERANCE, PortfolioLoadError, load


@pytest.fixture
def master() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["005930", "000660", "035420"],
            "name_ko": ["삼성전자", "SK하이닉스", "NAVER"],
            "name_en": ["Samsung Electronics", "SK Hynix", "NAVER"],
        }
    )


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "portfolio.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_valid_file_resolves_all(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "005930": 0.30
  "SK하이닉스": 0.20
  "NAVER": 0.10
drift_tolerance: 0.02
""",
    )
    pf = load(path, master_df=master)
    assert pf.broker == "KIS"
    assert pf.holdings == {
        Symbol("005930"): Decimal("0.30"),
        Symbol("000660"): Decimal("0.20"),
        Symbol("035420"): Decimal("0.10"),
    }
    assert pf.drift_tolerance == Decimal("0.02")


def test_sum_over_one_raises(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "005930": 0.7
  "000660": 0.4
""",
    )
    with pytest.raises(PortfolioLoadError, match=r"sum of weights > 1\.0"):
        load(path, master_df=master)


def test_sum_exactly_one_ok(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "005930": 0.6
  "000660": 0.4
""",
    )
    pf = load(path, master_df=master)
    assert sum(pf.holdings.values()) == Decimal("1.0")


def test_duplicate_after_resolution_raises(tmp_path: Path, master: pd.DataFrame) -> None:
    # Two keys resolve to the same Symbol — code + Korean name for Samsung
    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "005930": 0.3
  "삼성전자": 0.2
""",
    )
    with pytest.raises(PortfolioLoadError, match="duplicate symbol"):
        load(path, master_df=master)


def test_drift_tolerance_default(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "005930": 0.5
""",
    )
    pf = load(path, master_df=master)
    assert pf.drift_tolerance == DEFAULT_DRIFT_TOLERANCE


def test_missing_broker_raises(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
holdings:
  "005930": 0.5
""",
    )
    with pytest.raises(PortfolioLoadError, match="'broker'"):
        load(path, master_df=master)


def test_weight_out_of_range_raises(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "005930": 1.5
""",
    )
    with pytest.raises(PortfolioLoadError, match="not in"):
        load(path, master_df=master)


def test_unknown_name_propagates(tmp_path: Path, master: pd.DataFrame) -> None:
    from trader.tickers.resolver import UnknownTickerError

    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "Nonexistent Co": 0.1
""",
    )
    with pytest.raises(UnknownTickerError):
        load(path, master_df=master)


def test_empty_holdings_ok(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
broker: KIS
holdings: {}
""",
    )
    pf = load(path, master_df=master)
    assert pf.holdings == {}


def test_drift_tolerance_out_of_range_raises(tmp_path: Path, master: pd.DataFrame) -> None:
    path = _write(
        tmp_path,
        """
broker: KIS
holdings:
  "005930": 0.5
drift_tolerance: 2.0
""",
    )
    with pytest.raises(PortfolioLoadError, match="drift_tolerance"):
        load(path, master_df=master)

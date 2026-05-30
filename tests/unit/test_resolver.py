from __future__ import annotations

import pandas as pd
import pytest

from trader.domain.types import Symbol
from trader.tickers.resolver import AmbiguousTickerError, UnknownTickerError, resolve


@pytest.fixture
def master() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["005930", "000660", "035420", "999991", "999992"],
            "name_ko": ["삼성전자", "SK하이닉스", "NAVER", "쌍둥이", "쌍둥이"],
            "name_en": ["Samsung Electronics", "SK Hynix", "NAVER", "", ""],
        }
    )


def test_korean_name_resolves(master: pd.DataFrame) -> None:
    assert resolve(master, "삼성전자") == Symbol("005930")


def test_english_name_resolves(master: pd.DataFrame) -> None:
    assert resolve(master, "Samsung Electronics") == Symbol("005930")


def test_six_digit_pass_through(master: pd.DataFrame) -> None:
    assert resolve(master, "005930") == Symbol("005930")


def test_six_digit_pass_through_does_not_validate_against_master(master: pd.DataFrame) -> None:
    # raw codes are accepted unconditionally per spec
    assert resolve(master, "111111") == Symbol("111111")


def test_unknown_raises(master: pd.DataFrame) -> None:
    with pytest.raises(UnknownTickerError) as e:
        resolve(master, "Definitely Not A Company")
    assert e.value.query == "Definitely Not A Company"


def test_ambiguous_raises_with_both_candidates(master: pd.DataFrame) -> None:
    with pytest.raises(AmbiguousTickerError) as e:
        resolve(master, "쌍둥이")
    assert sorted(e.value.candidates) == ["999991", "999992"]


def test_whitespace_tolerance(master: pd.DataFrame) -> None:
    assert resolve(master, "  삼성전자  ") == Symbol("005930")


def test_case_tolerance(master: pd.DataFrame) -> None:
    assert resolve(master, "samsung electronics") == Symbol("005930")
    assert resolve(master, "SAMSUNG ELECTRONICS") == Symbol("005930")


def test_blank_query_raises(master: pd.DataFrame) -> None:
    with pytest.raises(UnknownTickerError):
        resolve(master, "   ")


def test_short_numeric_not_pass_through(master: pd.DataFrame) -> None:
    # 5-digit string is not a code; falls through to name lookup → unknown
    with pytest.raises(UnknownTickerError):
        resolve(master, "00593")

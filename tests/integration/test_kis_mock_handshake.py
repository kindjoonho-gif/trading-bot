"""Live handshake test against KIS Mock Account (모의투자).

Gated by `KIS_INTEGRATION=1` so it never runs by default.
Requires real `.env` credentials at the project root.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

from trader.brokers.kis import KISBroker
from trader.config.settings import Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("KIS_INTEGRATION") != "1",
    reason="set KIS_INTEGRATION=1 to run live mock handshake",
)


@pytest.fixture
def mock_settings() -> Settings:
    s = Settings()  # type: ignore[call-arg]
    if s.KIS_ENV != "mock":
        pytest.skip("integration test only runs with KIS_ENV=mock")
    return s


@pytest.mark.asyncio
async def test_mock_auth_and_get_cash(mock_settings: Settings, tmp_path) -> None:
    async with KISBroker(mock_settings, cache_dir=tmp_path) as broker:
        cash = await broker.get_cash()
        assert isinstance(cash, Decimal)
        assert cash >= 0
        assert (tmp_path / "kis_token_mock.json").exists()


@pytest.mark.asyncio
async def test_token_cache_hit_on_second_call(mock_settings: Settings, tmp_path) -> None:
    async with KISBroker(mock_settings, cache_dir=tmp_path) as b1:
        await b1.get_cash()
    async with KISBroker(mock_settings, cache_dir=tmp_path) as b2:
        await b2.get_cash()

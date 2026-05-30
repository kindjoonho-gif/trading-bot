from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from trader.brokers.kis import KISApiError, KISBroker
from trader.config.settings import Settings
from trader.domain.types import Side

MOCK_BASE = "https://openapivts.koreainvestment.com:29443"


def make_settings(**overrides: str) -> Settings:
    base = {
        "KIS_ENV": "mock",
        "KIS_MOCK_APP_KEY": "mock_key",
        "KIS_MOCK_APP_SECRET": "mock_secret",
        "KIS_MOCK_ACCOUNT_NO": "12345678-01",
        "KIS_REAL_APP_KEY": "real_key",
        "KIS_REAL_APP_SECRET": "real_secret",
        "KIS_REAL_ACCOUNT_NO": "87654321-01",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def broker_factory(tmp_path: Path):
    def _factory(client: httpx.AsyncClient) -> KISBroker:
        return KISBroker(make_settings(), cache_dir=tmp_path, http_client=client)
    return _factory


def _fill_row(**overrides: str) -> dict[str, str]:
    base = {
        "odno": "0000000001",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "tot_ccld_qty": "5",
        "avg_prvs": "70000",
        "ord_dt": "20260530",
        "ord_tmd": "100100",
    }
    base.update(overrides)
    return base


class TestListFills:
    @pytest.mark.asyncio
    async def test_populated_single_page(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "output1": [
                            _fill_row(),
                            _fill_row(odno="0000000002", sll_buy_dvsn_cd="01", pdno="000660"),
                        ],
                        "output2": {},
                    },
                    headers={"tr_cont": "D"},
                )
                broker = broker_factory(c)
                fills = await broker.list_fills(date(2026, 5, 1), date(2026, 5, 31))
                assert len(fills) == 2
                assert fills[0].symbol == "005930"
                assert fills[0].side is Side.BUY
                assert fills[0].quantity == Decimal("5")
                assert fills[0].fill_price == Decimal("70000")
                assert fills[0].fees == Decimal("0")
                assert fills[1].symbol == "000660"
                assert fills[1].side is Side.SELL

    @pytest.mark.asyncio
    async def test_empty_window(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200,
                    json={"rt_cd": "0", "output1": [], "output2": {}},
                    headers={"tr_cont": "D"},
                )
                broker = broker_factory(c)
                fills = await broker.list_fills(date(2026, 5, 1), date(2026, 5, 31))
                assert fills == []

    @pytest.mark.asyncio
    async def test_multi_page_continuation(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                route = router.get(
                    "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
                ).mock(
                    side_effect=[
                        httpx.Response(
                            200,
                            json={
                                "rt_cd": "0",
                                "output1": [_fill_row()],
                                "output2": {},
                                "ctx_area_fk100": "fk_page1",
                                "ctx_area_nk100": "nk_page1",
                            },
                            headers={"tr_cont": "M"},
                        ),
                        httpx.Response(
                            200,
                            json={
                                "rt_cd": "0",
                                "output1": [_fill_row(odno="0000000002")],
                                "output2": {},
                            },
                            headers={"tr_cont": "D"},
                        ),
                    ]
                )
                broker = broker_factory(c)
                fills = await broker.list_fills(date(2026, 5, 1), date(2026, 5, 31))
                assert len(fills) == 2
                assert route.call_count == 2

    @pytest.mark.asyncio
    async def test_rt_cd_nonzero_raises(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200, json={"rt_cd": "1", "msg_cd": "EGW00444", "msg1": "bad range"}
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="EGW00444"):
                    await broker.list_fills(date(2026, 5, 1), date(2026, 5, 31))


class TestRealizedPnL:
    @pytest.mark.asyncio
    async def test_populated(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get(
                    "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"
                ).respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "output1": [
                            {
                                "pdno": "005930",
                                "trad_qty": "10",
                                "buy_amt": "700000",
                                "sll_amt": "750000",
                                "rlzt_pfls": "50000",
                                "pfls_rt": "7.14",
                            },
                        ],
                        "output2": {
                            "buy_amt_smtl": "700000",
                            "sll_amt_smtl": "750000",
                            "rlzt_pfls_smtl": "50000",
                        },
                    },
                    headers={"tr_cont": "D"},
                )
                broker = broker_factory(c)
                summary = await broker.realized_pnl(date(2026, 5, 1), date(2026, 5, 31))
                assert len(summary.rows) == 1
                r = summary.rows[0]
                assert r.symbol == "005930"
                assert r.realized_pnl == Decimal("50000")
                assert summary.total_realized_pnl == Decimal("50000")
                assert summary.total_buy_amount == Decimal("700000")

    @pytest.mark.asyncio
    async def test_empty_window(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get(
                    "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"
                ).respond(
                    200,
                    json={"rt_cd": "0", "output1": [], "output2": {}},
                    headers={"tr_cont": "D"},
                )
                broker = broker_factory(c)
                summary = await broker.realized_pnl(date(2026, 5, 1), date(2026, 5, 31))
                assert summary.rows == ()
                assert summary.total_realized_pnl == Decimal("0")

    @pytest.mark.asyncio
    async def test_mock_unsupported_surfaces_api_error(self, broker_factory) -> None:
        """KIS mock typically doesn't support TTTC8715R; broker surfaces the
        KIS error verbatim so the page can degrade gracefully."""
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get(
                    "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"
                ).respond(
                    200,
                    json={
                        "rt_cd": "1",
                        "msg_cd": "EGW00555",
                        "msg1": "모의투자 미지원 TR입니다",
                    },
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="EGW00555"):
                    await broker.realized_pnl(date(2026, 5, 1), date(2026, 5, 31))

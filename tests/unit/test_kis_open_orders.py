from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from trader.brokers.kis import KISApiError, KISBroker
from trader.config.settings import Settings
from trader.domain.types import OrderId, OrderKind, OrderStatus, Side

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


def _open_row(**overrides: str) -> dict[str, str]:
    base = {
        "odno": "0000000001",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "ord_dvsn_cd": "00",
        "ord_qty": "10",
        "ord_unpr": "65000",
        "tot_ccld_qty": "0",
        "avg_prvs": "0",
        "cncl_yn": "N",
        "rfus_yn": "N",
        "ord_gno_brno": "00950",
    }
    base.update(overrides)
    return base


class TestListOpenOrders:
    @pytest.mark.asyncio
    async def test_populated(self, broker_factory) -> None:
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
                            _open_row(),
                            _open_row(odno="0000000002", sll_buy_dvsn_cd="01", pdno="000660"),
                        ],
                        "output2": {},
                    },
                    headers={"tr_cont": "D"},
                )
                broker = broker_factory(c)
                orders = await broker.list_open_orders()
                assert len(orders) == 2
                assert orders[0].symbol == "005930"
                assert orders[0].side is Side.BUY
                assert orders[0].kind is OrderKind.LIMIT
                assert orders[0].quantity == Decimal("10")
                assert orders[0].status is OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_empty(self, broker_factory) -> None:
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
                assert await broker.list_open_orders() == []

    @pytest.mark.asyncio
    async def test_cancelled_or_rejected_rows_filtered(self, broker_factory) -> None:
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
                            _open_row(),
                            _open_row(odno="0000000002", cncl_yn="Y"),
                            _open_row(odno="0000000003", rfus_yn="Y"),
                        ],
                        "output2": {},
                    },
                    headers={"tr_cont": "D"},
                )
                broker = broker_factory(c)
                orders = await broker.list_open_orders()
                assert [o.order_id for o in orders] == ["0000000001"]

    @pytest.mark.asyncio
    async def test_multi_page(self, broker_factory) -> None:
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
                                "output1": [_open_row()],
                                "output2": {},
                                "ctx_area_fk100": "fk_p1",
                                "ctx_area_nk100": "nk_p1",
                            },
                            headers={"tr_cont": "M"},
                        ),
                        httpx.Response(
                            200,
                            json={
                                "rt_cd": "0",
                                "output1": [_open_row(odno="0000000002")],
                                "output2": {},
                            },
                            headers={"tr_cont": "D"},
                        ),
                    ]
                )
                broker = broker_factory(c)
                orders = await broker.list_open_orders()
                assert [o.order_id for o in orders] == ["0000000001", "0000000002"]
                assert route.call_count == 2


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_happy_path(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200,
                    json={"rt_cd": "0", "output1": [_open_row()], "output2": {}},
                    headers={"tr_cont": "D"},
                )
                cancel_route = router.post(
                    "/uapi/domestic-stock/v1/trading/order-rvsecncl"
                ).respond(200, json={"rt_cd": "0", "output": {"ODNO": "0000000001"}})
                broker = broker_factory(c)
                await broker.cancel_order(OrderId("0000000001"))
                body = json.loads(cancel_route.calls[0].request.content)
                assert body["ORGN_ODNO"] == "0000000001"
                assert body["RVSE_CNCL_DVSN_CD"] == "02"
                assert body["QTY_ALL_ORD_YN"] == "Y"
                assert body["KRX_FWDG_ORD_ORGNO"] == "00950"
                assert body["EXCG_ID_DVSN_CD"] == "KRX"

    @pytest.mark.asyncio
    async def test_not_found_raises(self, broker_factory) -> None:
        """Unknown OrderId (or already-filled, which falls off the open list)."""
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
                with pytest.raises(KISApiError, match="not in open orders"):
                    await broker.cancel_order(OrderId("0000000999"))

    @pytest.mark.asyncio
    async def test_broker_reject_after_lookup(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200,
                    json={"rt_cd": "0", "output1": [_open_row()], "output2": {}},
                    headers={"tr_cont": "D"},
                )
                router.post(
                    "/uapi/domestic-stock/v1/trading/order-rvsecncl"
                ).respond(200, json={"rt_cd": "1", "msg_cd": "EGW00333", "msg1": "too late"})
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="EGW00333"):
                    await broker.cancel_order(OrderId("0000000001"))

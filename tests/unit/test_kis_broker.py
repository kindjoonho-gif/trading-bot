from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from trader.brokers.kis import KISApiError, KISAuthError, KISBroker, _TokenCache
from trader.config.settings import Settings
from trader.domain.types import OrderId, OrderKind, OrderStatus, Side, Symbol

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


class TestTokenCache:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        cache = _TokenCache(env="mock", cache_dir=tmp_path)
        expires = datetime.now(UTC) + timedelta(hours=10)
        cache.save("tok", expires)
        loaded = cache.load()
        assert loaded is not None
        assert loaded[0] == "tok"

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        assert _TokenCache(env="mock", cache_dir=tmp_path).load() is None

    def test_load_corrupt_returns_none(self, tmp_path: Path) -> None:
        cache = _TokenCache(env="mock", cache_dir=tmp_path)
        cache.path.parent.mkdir(parents=True, exist_ok=True)
        cache.path.write_text("not json", encoding="utf-8")
        assert cache.load() is None

    def test_load_expired_returns_none(self, tmp_path: Path) -> None:
        cache = _TokenCache(env="mock", cache_dir=tmp_path)
        expired = datetime.now(UTC) - timedelta(seconds=10)
        cache.save("tok", expired)
        assert cache.load() is None

    def test_load_within_refresh_buffer_returns_none(self, tmp_path: Path) -> None:
        cache = _TokenCache(env="mock", cache_dir=tmp_path)
        almost = datetime.now(UTC) + timedelta(seconds=30)
        cache.save("tok", almost)
        assert cache.load() is None

    def test_clear_removes_file(self, tmp_path: Path) -> None:
        cache = _TokenCache(env="mock", cache_dir=tmp_path)
        cache.save("tok", datetime.now(UTC) + timedelta(hours=1))
        cache.clear()
        assert not cache.path.exists()


@pytest.fixture
def broker_factory(tmp_path: Path):
    def _factory(client: httpx.AsyncClient, settings: Settings | None = None) -> KISBroker:
        return KISBroker(
            settings or make_settings(),
            cache_dir=tmp_path,
            http_client=client,
        )
    return _factory


class TestIssueToken:
    @pytest.mark.asyncio
    async def test_success_returns_token_and_expiry(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200,
                    json={
                        "access_token": "abc123",
                        "expires_in": 86400,
                        "token_type": "Bearer",
                    },
                )
                broker = broker_factory(c)
                tok, exp = await broker._issue_token()
                assert tok == "abc123"
                assert exp > datetime.now(UTC) + timedelta(hours=23)

    @pytest.mark.asyncio
    async def test_uses_kis_expired_field_when_no_expires_in(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200,
                    json={
                        "access_token": "abc123",
                        "access_token_token_expired": "2099-01-01 09:00:00",
                    },
                )
                broker = broker_factory(c)
                _, exp = await broker._issue_token()
                assert exp.year == 2099

    @pytest.mark.asyncio
    async def test_4xx_raises_auth_error_no_retry(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                route = router.post("/oauth2/tokenP").respond(403, text="forbidden")
                broker = broker_factory(c)
                with pytest.raises(KISAuthError):
                    await broker._issue_token()
                assert route.call_count == 1

    @pytest.mark.asyncio
    async def test_missing_token_in_200_raises(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(200, json={"expires_in": 86400})
                broker = broker_factory(c)
                with pytest.raises(KISAuthError, match="no access_token"):
                    await broker._issue_token()


class TestGetTokenCaching:
    @pytest.mark.asyncio
    async def test_first_call_issues_then_caches(self, tmp_path: Path) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                route = router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                broker = KISBroker(make_settings(), cache_dir=tmp_path, http_client=c)
                t1 = await broker._get_token()
                t2 = await broker._get_token()
                assert t1 == t2 == "tok"
                assert route.call_count == 1
                assert (tmp_path / "kis_token_mock.json").exists()

    @pytest.mark.asyncio
    async def test_picks_up_existing_disk_cache(self, tmp_path: Path) -> None:
        expires = datetime.now(UTC) + timedelta(hours=10)
        (tmp_path / "kis_token_mock.json").write_text(
            json.dumps({"access_token": "from_disk", "expires_at": expires.isoformat()}),
            encoding="utf-8",
        )
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE, assert_all_called=False) as router:
                route = router.post("/oauth2/tokenP")
                broker = KISBroker(make_settings(), cache_dir=tmp_path, http_client=c)
                tok = await broker._get_token()
                assert tok == "from_disk"
                assert route.call_count == 0


class TestGetCash:
    @pytest.mark.asyncio
    async def test_returns_decimal_from_dnca_tot_amt(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-balance").respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "msg_cd": "OK",
                        "msg1": "",
                        "output1": [],
                        "output2": [{"dnca_tot_amt": "1234567"}],
                    },
                )
                broker = broker_factory(c)
                cash = await broker.get_cash()
                assert cash == Decimal("1234567")

    @pytest.mark.asyncio
    async def test_rt_cd_nonzero_raises_api_error(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-balance").respond(
                    200,
                    json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "bad account"},
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="EGW00123"):
                    await broker.get_cash()

    @pytest.mark.asyncio
    async def test_401_triggers_reauth_and_retry(self, tmp_path: Path) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                token_route = router.post("/oauth2/tokenP").mock(
                    side_effect=[
                        httpx.Response(200, json={"access_token": "t1", "expires_in": 86400}),
                        httpx.Response(200, json={"access_token": "t2", "expires_in": 86400}),
                    ]
                )
                balance_route = router.get(
                    "/uapi/domestic-stock/v1/trading/inquire-balance"
                ).mock(
                    side_effect=[
                        httpx.Response(401, text="unauthorized"),
                        httpx.Response(
                            200,
                            json={
                                "rt_cd": "0",
                                "msg_cd": "OK",
                                "msg1": "",
                                "output1": [],
                                "output2": [{"dnca_tot_amt": "500"}],
                            },
                        ),
                    ]
                )
                broker = KISBroker(make_settings(), cache_dir=tmp_path, http_client=c)
                cash = await broker.get_cash()
                assert cash == Decimal("500")
                assert balance_route.call_count == 2
                assert token_route.call_count == 2
                assert (tmp_path / "kis_token_mock.json").exists()


class TestGetPositions:
    @pytest.mark.asyncio
    async def test_parses_output1_rows(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-balance").respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "msg_cd": "OK",
                        "msg1": "",
                        "output1": [
                            {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "70000"},
                            {"pdno": "000660", "hldg_qty": "5", "pchs_avg_pric": "120000.5"},
                        ],
                        "output2": [{"dnca_tot_amt": "0"}],
                    },
                )
                broker = broker_factory(c)
                positions = await broker.get_positions()
                assert len(positions) == 2
                assert positions[0].symbol == "005930"
                assert positions[0].quantity == Decimal("10")
                assert positions[0].avg_cost == Decimal("70000")
                assert positions[1].symbol == "000660"
                assert positions[1].quantity == Decimal("5")
                assert positions[1].avg_cost == Decimal("120000.5")

    @pytest.mark.asyncio
    async def test_skips_zero_quantity_rows(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-balance").respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "msg_cd": "OK",
                        "msg1": "",
                        "output1": [
                            {"pdno": "005930", "hldg_qty": "10", "pchs_avg_pric": "70000"},
                            {"pdno": "000660", "hldg_qty": "0", "pchs_avg_pric": "0"},
                        ],
                        "output2": [{"dnca_tot_amt": "0"}],
                    },
                )
                broker = broker_factory(c)
                positions = await broker.get_positions()
                assert len(positions) == 1
                assert positions[0].symbol == "005930"

    @pytest.mark.asyncio
    async def test_empty_output1_returns_empty_list(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-balance").respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "msg_cd": "OK",
                        "msg1": "",
                        "output1": [],
                        "output2": [{"dnca_tot_amt": "0"}],
                    },
                )
                broker = broker_factory(c)
                assert await broker.get_positions() == []

    @pytest.mark.asyncio
    async def test_rt_cd_nonzero_raises_api_error(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-balance").respond(
                    200,
                    json={"rt_cd": "1", "msg_cd": "EGW00999", "msg1": "boom"},
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="EGW00999"):
                    await broker.get_positions()


class TestGetQuote:
    @pytest.mark.asyncio
    async def test_parses_bid_ask_last_from_output1_output2(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get(
                    "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
                ).respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "msg_cd": "OK",
                        "msg1": "",
                        "output1": {"bidp1": "69900", "askp1": "70000"},
                        "output2": {"stck_prpr": "69950"},
                    },
                )
                broker = broker_factory(c)
                q = await broker.get_quote(Symbol("005930"))
                assert q.symbol == "005930"
                assert q.bid == Decimal("69900")
                assert q.ask == Decimal("70000")
                assert q.last == Decimal("69950")

    @pytest.mark.asyncio
    async def test_falls_back_to_antc_cnpr_when_stck_prpr_missing(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get(
                    "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
                ).respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "output1": {"bidp1": "100", "askp1": "101"},
                        "output2": {"antc_cnpr": "100"},
                    },
                )
                broker = broker_factory(c)
                q = await broker.get_quote(Symbol("005930"))
                assert q.last == Decimal("100")

    @pytest.mark.asyncio
    async def test_rt_cd_nonzero_raises_api_error(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get(
                    "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
                ).respond(
                    200,
                    json={"rt_cd": "1", "msg_cd": "EGW00777", "msg1": "bad symbol"},
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="EGW00777"):
                    await broker.get_quote(Symbol("999999"))

    @pytest.mark.asyncio
    async def test_401_triggers_reauth_and_retry(self, tmp_path: Path) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").mock(
                    side_effect=[
                        httpx.Response(200, json={"access_token": "t1", "expires_in": 86400}),
                        httpx.Response(200, json={"access_token": "t2", "expires_in": 86400}),
                    ]
                )
                quote_route = router.get(
                    "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
                ).mock(
                    side_effect=[
                        httpx.Response(401, text="unauthorized"),
                        httpx.Response(
                            200,
                            json={
                                "rt_cd": "0",
                                "output1": {"bidp1": "1", "askp1": "2"},
                                "output2": {"stck_prpr": "2"},
                            },
                        ),
                    ]
                )
                broker = KISBroker(make_settings(), cache_dir=tmp_path, http_client=c)
                q = await broker.get_quote(Symbol("005930"))
                assert q.last == Decimal("2")
                assert quote_route.call_count == 2


class TestPlaceOrder:
    @pytest.mark.asyncio
    async def test_market_buy_returns_order_id_from_odno(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                route = router.post("/uapi/domestic-stock/v1/trading/order-cash").respond(
                    200,
                    json={
                        "rt_cd": "0",
                        "msg_cd": "OK",
                        "msg1": "",
                        "output": {"KRX_FWDG_ORD_ORGNO": "00950", "ODNO": "0000123456"},
                    },
                )
                broker = broker_factory(c)
                oid = await broker.place_order(
                    Symbol("005930"), Side.BUY, OrderKind.MARKET, Decimal("1")
                )
                assert oid == "0000123456"
                body = json.loads(route.calls[0].request.content)
                assert body["PDNO"] == "005930"
                assert body["ORD_DVSN"] == "01"
                assert body["ORD_QTY"] == "1"
                assert body["ORD_UNPR"] == "0"
                assert body["EXCG_ID_DVSN_CD"] == "KRX"

    @pytest.mark.asyncio
    async def test_limit_sell_sends_price_and_sll_type(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                route = router.post("/uapi/domestic-stock/v1/trading/order-cash").respond(
                    200, json={"rt_cd": "0", "output": {"ODNO": "0000999"}}
                )
                broker = broker_factory(c)
                await broker.place_order(
                    Symbol("005930"),
                    Side.SELL,
                    OrderKind.LIMIT,
                    Decimal("5"),
                    Decimal("70000"),
                )
                body = json.loads(route.calls[0].request.content)
                assert body["ORD_DVSN"] == "00"
                assert body["ORD_UNPR"] == "70000"
                assert body["SLL_TYPE"] == "01"

    @pytest.mark.asyncio
    async def test_market_with_price_rejected_locally(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            broker = broker_factory(c)
            with pytest.raises(ValueError, match="Market"):
                await broker.place_order(
                    Symbol("005930"),
                    Side.BUY,
                    OrderKind.MARKET,
                    Decimal("1"),
                    Decimal("70000"),
                )

    @pytest.mark.asyncio
    async def test_limit_without_price_rejected_locally(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            broker = broker_factory(c)
            with pytest.raises(ValueError, match="Limit"):
                await broker.place_order(
                    Symbol("005930"), Side.BUY, OrderKind.LIMIT, Decimal("1")
                )

    @pytest.mark.asyncio
    async def test_rt_cd_nonzero_raises_api_error(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.post("/uapi/domestic-stock/v1/trading/order-cash").respond(
                    200, json={"rt_cd": "1", "msg_cd": "EGW00666", "msg1": "rejected"}
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="EGW00666"):
                    await broker.place_order(
                        Symbol("005930"), Side.BUY, OrderKind.MARKET, Decimal("1")
                    )

    @pytest.mark.asyncio
    async def test_missing_odno_raises(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.post("/uapi/domestic-stock/v1/trading/order-cash").respond(
                    200, json={"rt_cd": "0", "output": {}}
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="no ODNO"):
                    await broker.place_order(
                        Symbol("005930"), Side.BUY, OrderKind.MARKET, Decimal("1")
                    )

    @pytest.mark.asyncio
    async def test_nxt_exchange_sent_when_configured(self, tmp_path: Path) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                route = router.post("/uapi/domestic-stock/v1/trading/order-cash").respond(
                    200, json={"rt_cd": "0", "output": {"ODNO": "0000999"}}
                )
                broker = KISBroker(
                    make_settings(), cache_dir=tmp_path, http_client=c, exchange="NXT"
                )
                await broker.place_order(
                    Symbol("005930"), Side.BUY, OrderKind.MARKET, Decimal("1")
                )
                body = json.loads(route.calls[0].request.content)
                assert body["EXCG_ID_DVSN_CD"] == "NXT"


def _order_row(**overrides: str) -> dict[str, str]:
    base = {
        "odno": "0000123456",
        "pdno": "005930",
        "sll_buy_dvsn_cd": "02",
        "ord_dvsn_cd": "01",
        "ord_qty": "10",
        "ord_unpr": "0",
        "tot_ccld_qty": "0",
        "avg_prvs": "0",
        "cncl_yn": "N",
        "rfus_yn": "N",
    }
    base.update(overrides)
    return base


class TestGetOrder:
    @staticmethod
    def _mock_ccld_response(rows: list[dict[str, str]]) -> dict[str, object]:
        return {"rt_cd": "0", "msg_cd": "OK", "msg1": "", "output1": rows, "output2": {}}

    @pytest.mark.asyncio
    async def test_pending_when_no_fills(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200, json=self._mock_ccld_response([_order_row()])
                )
                broker = broker_factory(c)
                order = await broker.get_order(OrderId("0000123456"))
                assert order.status is OrderStatus.PENDING
                assert order.filled_quantity == Decimal("0")
                assert order.avg_fill_price is None
                assert order.side is Side.BUY
                assert order.kind is OrderKind.MARKET

    @pytest.mark.asyncio
    async def test_partial_when_some_filled(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200,
                    json=self._mock_ccld_response(
                        [_order_row(tot_ccld_qty="3", avg_prvs="70000")]
                    ),
                )
                broker = broker_factory(c)
                order = await broker.get_order(OrderId("0000123456"))
                assert order.status is OrderStatus.PARTIAL
                assert order.filled_quantity == Decimal("3")
                assert order.avg_fill_price == Decimal("70000")

    @pytest.mark.asyncio
    async def test_filled_when_all_filled(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200,
                    json=self._mock_ccld_response(
                        [_order_row(tot_ccld_qty="10", avg_prvs="71000")]
                    ),
                )
                broker = broker_factory(c)
                order = await broker.get_order(OrderId("0000123456"))
                assert order.status is OrderStatus.FILLED
                assert order.filled_quantity == Decimal("10")
                assert order.avg_fill_price == Decimal("71000")

    @pytest.mark.asyncio
    async def test_cancelled_when_cncl_yn_y(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200, json=self._mock_ccld_response([_order_row(cncl_yn="Y")])
                )
                broker = broker_factory(c)
                order = await broker.get_order(OrderId("0000123456"))
                assert order.status is OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_rejected_when_rfus_yn_y(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200, json=self._mock_ccld_response([_order_row(rfus_yn="Y")])
                )
                broker = broker_factory(c)
                order = await broker.get_order(OrderId("0000123456"))
                assert order.status is OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_unknown_odno_raises(self, broker_factory) -> None:
        async with httpx.AsyncClient(base_url=MOCK_BASE) as c:
            with respx.mock(base_url=MOCK_BASE) as router:
                router.post("/oauth2/tokenP").respond(
                    200, json={"access_token": "tok", "expires_in": 86400}
                )
                router.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld").respond(
                    200, json=self._mock_ccld_response([])
                )
                broker = broker_factory(c)
                with pytest.raises(KISApiError, match="not found"):
                    await broker.get_order(OrderId("0000999999"))

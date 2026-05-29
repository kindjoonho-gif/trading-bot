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

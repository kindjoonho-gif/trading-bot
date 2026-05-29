from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from trader.brokers.base import Broker
from trader.config.settings import Settings
from trader.domain.money import to_decimal
from trader.domain.types import Order, OrderId, OrderKind, Position, Quote, Side, Symbol

TR_ID_BALANCE_MOCK = "VTTC8434R"
TR_ID_BALANCE_REAL = "TTTC8434R"

_TOKEN_REFRESH_BUFFER_SECONDS = 60


class KISAuthError(RuntimeError):
    pass


class KISApiError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"KIS {code}: {message}")
        self.code = code
        self.message = message


class _TokenCache:
    """On-disk JSON cache with TTL. One file per KIS_ENV."""

    def __init__(self, env: str, cache_dir: Path) -> None:
        self.env = env
        self.path = cache_dir / f"kis_token_{env}.json"

    def load(self) -> tuple[str, datetime] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            token: str = data["access_token"]
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (KeyError, ValueError, json.JSONDecodeError):
            return None
        if expires_at - timedelta(seconds=_TOKEN_REFRESH_BUFFER_SECONDS) <= datetime.now(UTC):
            return None
        return token, expires_at

    def save(self, token: str, expires_at: datetime) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"access_token": token, "expires_at": expires_at.isoformat()}),
            encoding="utf-8",
        )

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


class KISBroker(Broker):
    """KIS REST adapter. Phase A slice I1: auth, token cache, get_cash only.

    Other Broker methods raise NotImplementedError until later slices land.
    """

    def __init__(
        self,
        settings: Settings,
        cache_dir: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        settings.require_credentials()
        self._settings = settings
        self._cache = _TokenCache(
            env=settings.KIS_ENV,
            cache_dir=cache_dir or Path(".cache"),
        )
        self._client = http_client or httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        self._owns_client = http_client is None
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> KISBroker:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ---- Auth ----

    async def _get_token(self) -> str:
        buffer = timedelta(seconds=_TOKEN_REFRESH_BUFFER_SECONDS)
        if (
            self._token
            and self._token_expires_at
            and self._token_expires_at - buffer > datetime.now(UTC)
        ):
            return self._token

        cached = self._cache.load()
        if cached is not None:
            self._token, self._token_expires_at = cached
            return self._token

        token, expires_at = await self._issue_token()
        self._token = token
        self._token_expires_at = expires_at
        self._cache.save(token, expires_at)
        return token

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _issue_token(self) -> tuple[str, datetime]:
        resp = await self._client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._settings.app_key,
                "appsecret": self._settings.app_secret,
            },
        )
        if resp.status_code != 200:
            raise KISAuthError(f"token issue failed: HTTP {resp.status_code} body={resp.text!r}")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise KISAuthError(f"no access_token in response: {data!r}")
        expires_in = int(data.get("expires_in", 0))
        if expires_in > 0:
            expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        elif "access_token_token_expired" in data:
            naive = datetime.strptime(data["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
            expires_at = naive.replace(tzinfo=timezone(timedelta(hours=9))).astimezone(UTC)
        else:
            expires_at = datetime.now(UTC) + timedelta(hours=23)
        return token, expires_at

    async def _headers(self, tr_id: str) -> dict[str, str]:
        token = await self._get_token()
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._settings.app_key,
            "appsecret": self._settings.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    # ---- Endpoints ----

    async def get_cash(self) -> Decimal:
        cano, prdt = self._settings.account
        tr_id = TR_ID_BALANCE_MOCK if self._settings.KIS_ENV == "mock" else TR_ID_BALANCE_REAL
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        url = "/uapi/domestic-stock/v1/trading/inquire-balance"
        resp = await self._client.get(url, headers=await self._headers(tr_id), params=params)
        if resp.status_code == 401:
            self._token = None
            self._cache.clear()
            resp = await self._client.get(url, headers=await self._headers(tr_id), params=params)
        if resp.status_code != 200:
            raise KISApiError(str(resp.status_code), resp.text)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))
        output2 = data.get("output2") or []
        if not output2:
            return Decimal("0")
        return to_decimal(output2[0].get("dnca_tot_amt", "0"))

    # ---- Phase A slices to come ----

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError("lands in slice I2 (#4)")

    async def get_quote(self, symbol: Symbol) -> Quote:
        raise NotImplementedError("lands in slice I2 (#4)")

    async def place_order(
        self,
        symbol: Symbol,
        side: Side,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None = None,
    ) -> OrderId:
        raise NotImplementedError("lands in slice I3 (#5)")

    async def get_order(self, order_id: OrderId) -> Order:
        raise NotImplementedError("lands in slice I3 (#5)")

    async def list_open_orders(self) -> list[Order]:
        raise NotImplementedError("lands in slice I4 (#7)")

    async def cancel_order(self, order_id: OrderId) -> None:
        raise NotImplementedError("lands in slice I4 (#7)")

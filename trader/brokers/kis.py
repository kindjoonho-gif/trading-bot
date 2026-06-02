from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from trader.brokers.base import Broker
from trader.config.settings import Settings
from trader.domain.money import to_decimal
from trader.domain.types import (
    Exchange,
    Fill,
    Order,
    OrderId,
    OrderKind,
    OrderStatus,
    Position,
    Quote,
    RealizedPnLRow,
    RealizedPnLSummary,
    Side,
    Symbol,
)

TR_ID_BALANCE_MOCK = "VTTC8434R"
TR_ID_BALANCE_REAL = "TTTC8434R"
TR_ID_QUOTE = "FHKST01010200"

TR_ID_ORDER_BUY_MOCK = "VTTC0012U"
TR_ID_ORDER_SELL_MOCK = "VTTC0011U"
TR_ID_ORDER_BUY_REAL = "TTTC0012U"
TR_ID_ORDER_SELL_REAL = "TTTC0011U"

TR_ID_INQUIRE_ORDERS_MOCK = "VTTC0081R"
TR_ID_INQUIRE_ORDERS_REAL = "TTTC0081R"

TR_ID_PERIOD_TRADE_PROFIT = "TTTC8715R"

TR_ID_RVSECNCL_MOCK = "VTTC0013U"
TR_ID_RVSECNCL_REAL = "TTTC0013U"

_ORD_DVSN_LIMIT = "00"
_ORD_DVSN_MARKET = "01"

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
        exchange: Exchange = "KRX",
    ) -> None:
        settings.require_credentials()
        self._settings = settings
        self._exchange: Exchange = exchange
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

    async def _balance_params(self) -> tuple[str, str, dict[str, str]]:
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
        return "/uapi/domestic-stock/v1/trading/inquire-balance", tr_id, params

    async def _request_balance(self) -> dict[str, Any]:
        url, tr_id, params = await self._balance_params()
        resp = await self._client.get(url, headers=await self._headers(tr_id), params=params)
        if resp.status_code == 401:
            self._token = None
            self._cache.clear()
            resp = await self._client.get(url, headers=await self._headers(tr_id), params=params)
        if resp.status_code != 200:
            raise KISApiError(str(resp.status_code), resp.text)
        data: dict[str, Any] = resp.json()
        if data.get("rt_cd") != "0":
            raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))
        return data

    async def get_cash(self) -> Decimal:
        data = await self._request_balance()
        output2 = data.get("output2") or []
        if not output2:
            return Decimal("0")
        return to_decimal(output2[0].get("dnca_tot_amt", "0"))

    async def get_positions(self) -> list[Position]:
        data = await self._request_balance()
        output1 = data.get("output1") or []
        positions: list[Position] = []
        for row in output1:
            qty = to_decimal(row.get("hldg_qty", "0"))
            if qty == 0:
                continue
            positions.append(
                Position(
                    symbol=Symbol(str(row["pdno"])),
                    quantity=qty,
                    avg_cost=to_decimal(row.get("pchs_avg_pric", "0")),
                )
            )
        return positions

    async def get_quote(self, symbol: Symbol) -> Quote:
        url = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": str(symbol)}
        resp = await self._client.get(
            url, headers=await self._headers(TR_ID_QUOTE), params=params
        )
        if resp.status_code == 401:
            self._token = None
            self._cache.clear()
            resp = await self._client.get(
                url, headers=await self._headers(TR_ID_QUOTE), params=params
            )
        if resp.status_code != 200:
            raise KISApiError(str(resp.status_code), resp.text)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))
        output1 = data.get("output1") or {}
        output2 = data.get("output2") or {}
        last_raw = output2.get("stck_prpr") or output2.get("antc_cnpr") or "0"
        return Quote(
            symbol=symbol,
            bid=to_decimal(output1.get("bidp1", "0")),
            ask=to_decimal(output1.get("askp1", "0")),
            last=to_decimal(last_raw),
        )

    async def place_order(
        self,
        symbol: Symbol,
        side: Side,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None = None,
    ) -> OrderId:
        if kind is OrderKind.MARKET and price is not None:
            raise ValueError("Market order must not have a price")
        if kind is OrderKind.LIMIT and price is None:
            raise ValueError("Limit order requires a price")
        cano, prdt = self._settings.account
        is_mock = self._settings.KIS_ENV == "mock"
        if side is Side.BUY:
            tr_id = TR_ID_ORDER_BUY_MOCK if is_mock else TR_ID_ORDER_BUY_REAL
        else:
            tr_id = TR_ID_ORDER_SELL_MOCK if is_mock else TR_ID_ORDER_SELL_REAL
        ord_dvsn = _ORD_DVSN_MARKET if kind is OrderKind.MARKET else _ORD_DVSN_LIMIT
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "PDNO": str(symbol),
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(int(quantity)),
            "ORD_UNPR": "0" if kind is OrderKind.MARKET else str(price),
            "EXCG_ID_DVSN_CD": self._exchange,
            "SLL_TYPE": "01" if side is Side.SELL else "",
            "CNDT_PRIC": "",
        }
        url = "/uapi/domestic-stock/v1/trading/order-cash"
        resp = await self._client.post(url, headers=await self._headers(tr_id), json=body)
        if resp.status_code == 401:
            self._token = None
            self._cache.clear()
            resp = await self._client.post(url, headers=await self._headers(tr_id), json=body)
        if resp.status_code != 200:
            raise KISApiError(str(resp.status_code), resp.text)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))
        output = data.get("output") or {}
        odno = output.get("ODNO") or output.get("odno")
        if not odno:
            raise KISApiError("?", f"no ODNO in order response: {data!r}")
        return OrderId(str(odno))

    async def get_order(self, order_id: OrderId) -> Order:
        cano, prdt = self._settings.account
        tr_id = (
            TR_ID_INQUIRE_ORDERS_MOCK
            if self._settings.KIS_ENV == "mock"
            else TR_ID_INQUIRE_ORDERS_REAL
        )
        today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "INQR_DVSN": "00",
            "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": str(order_id),
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": self._exchange,
        }
        url = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
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
        rows = data.get("output1") or []
        matched = [r for r in rows if str(r.get("odno", "")) == str(order_id)]
        if not matched:
            raise KISApiError("?", f"order {order_id} not found in today's orders")
        return _row_to_order(matched[0])

    async def list_open_orders(self) -> list[Order]:
        """Return today's unfilled orders (PENDING + PARTIAL).

        Uses inquire-daily-ccld with CCLD_DVSN='02' (unfilled) — mock-supported,
        today-only window. Multi-day stale orders are out of scope.
        """
        rows = await self._fetch_orders_for_today(ccld_dvsn="02")
        return [
            _row_to_order(r)
            for r in rows
            if (r.get("cncl_yn") or "N").upper() != "Y"
            and (r.get("rfus_yn") or r.get("rjct_yn") or "N").upper() != "Y"
        ]

    async def cancel_order(self, order_id: OrderId) -> None:
        """Look up the order in today's open list, then submit a full-quantity
        cancel via order-rvsecncl. Already-filled or unknown ID surfaces as
        KISApiError 'not found in open orders'.
        """
        rows = await self._fetch_orders_for_today(ccld_dvsn="02", odno=str(order_id))
        matched = [r for r in rows if str(r.get("odno", "")) == str(order_id)]
        if not matched:
            raise KISApiError(
                "?", f"order {order_id} not in open orders (already filled or unknown)"
            )
        row = matched[0]
        await asyncio.sleep(1.1)
        cano, prdt = self._settings.account
        tr_id = (
            TR_ID_RVSECNCL_MOCK if self._settings.KIS_ENV == "mock" else TR_ID_RVSECNCL_REAL
        )
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "KRX_FWDG_ORD_ORGNO": str(row.get("ord_gno_brno", "")),
            "ORGN_ODNO": str(order_id),
            "ORD_DVSN": str(row.get("ord_dvsn_cd", _ORD_DVSN_LIMIT)),
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(int(to_decimal(row.get("ord_qty", "0")))),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
            "EXCG_ID_DVSN_CD": self._exchange,
        }
        url = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
        resp = await self._client.post(url, headers=await self._headers(tr_id), json=body)
        if resp.status_code == 401:
            self._token = None
            self._cache.clear()
            resp = await self._client.post(
                url, headers=await self._headers(tr_id), json=body
            )
        if resp.status_code != 200:
            raise KISApiError(str(resp.status_code), resp.text)
        data = resp.json()
        if data.get("rt_cd") != "0":
            raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))

    async def _fetch_orders_for_today(
        self, *, ccld_dvsn: str, odno: str = ""
    ) -> list[dict[str, Any]]:
        """Shared helper: paginated GET inquire-daily-ccld for today only."""
        cano, prdt = self._settings.account
        tr_id = (
            TR_ID_INQUIRE_ORDERS_MOCK
            if self._settings.KIS_ENV == "mock"
            else TR_ID_INQUIRE_ORDERS_REAL
        )
        today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
        url = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        out: list[dict[str, Any]] = []
        fk100 = ""
        nk100 = ""
        tr_cont = ""
        for _ in range(10):
            params = {
                "CANO": cano,
                "ACNT_PRDT_CD": prdt,
                "INQR_STRT_DT": today,
                "INQR_END_DT": today,
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": "",
                "CCLD_DVSN": ccld_dvsn,
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": odno,
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": fk100,
                "CTX_AREA_NK100": nk100,
                "EXCG_ID_DVSN_CD": self._exchange,
            }
            headers = await self._headers(tr_id)
            if tr_cont:
                headers["tr_cont"] = tr_cont
            resp = await self._client.get(url, headers=headers, params=params)
            if resp.status_code == 401:
                self._token = None
                self._cache.clear()
                resp = await self._client.get(
                    url, headers=await self._headers(tr_id), params=params
                )
            if resp.status_code != 200:
                raise KISApiError(str(resp.status_code), resp.text)
            data = resp.json()
            if data.get("rt_cd") != "0":
                raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))
            out.extend(data.get("output1") or [])
            next_cont = resp.headers.get("tr_cont", "").upper()
            if next_cont not in {"M", "F"}:
                break
            tr_cont = "N"
            fk100 = data.get("ctx_area_fk100", "") or ""
            nk100 = data.get("ctx_area_nk100", "") or ""
        return out

    async def list_fills(self, start_date: date, end_date: date) -> list[Fill]:
        """Return all filled-order records in [start_date, end_date], following
        KIS pagination (tr_cont = M/F => next page, D/E => terminal).
        """
        cano, prdt = self._settings.account
        tr_id = (
            TR_ID_INQUIRE_ORDERS_MOCK
            if self._settings.KIS_ENV == "mock"
            else TR_ID_INQUIRE_ORDERS_REAL
        )
        url = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        fills: list[Fill] = []
        fk100 = ""
        nk100 = ""
        tr_cont = ""
        for _ in range(10):
            params = {
                "CANO": cano,
                "ACNT_PRDT_CD": prdt,
                "INQR_STRT_DT": start_date.strftime("%Y%m%d"),
                "INQR_END_DT": end_date.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": "",
                "CCLD_DVSN": "01",
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": fk100,
                "CTX_AREA_NK100": nk100,
                "EXCG_ID_DVSN_CD": self._exchange,
            }
            headers = await self._headers(tr_id)
            if tr_cont:
                headers["tr_cont"] = tr_cont
            resp = await self._client.get(url, headers=headers, params=params)
            if resp.status_code == 401:
                self._token = None
                self._cache.clear()
                resp = await self._client.get(
                    url, headers=await self._headers(tr_id), params=params
                )
            if resp.status_code != 200:
                raise KISApiError(str(resp.status_code), resp.text)
            data = resp.json()
            if data.get("rt_cd") != "0":
                raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))
            for row in data.get("output1") or []:
                fills.append(_row_to_fill(row))
            next_cont = resp.headers.get("tr_cont", "").upper()
            if next_cont not in {"M", "F"}:
                break
            tr_cont = "N"
            fk100 = data.get("ctx_area_fk100", "") or ""
            nk100 = data.get("ctx_area_nk100", "") or ""
        return fills

    async def realized_pnl(
        self, start_date: date, end_date: date
    ) -> RealizedPnLSummary:
        """Per-symbol realized P&L over [start_date, end_date] + grand total.

        Uses TR TTTC8715R (inquire-period-trade-profit). KIS docs do not list a
        mock TR; the call may raise KISApiError on KIS_ENV=mock.
        """
        cano, prdt = self._settings.account
        url = "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"
        rows: list[RealizedPnLRow] = []
        total_buy = Decimal("0")
        total_sell = Decimal("0")
        total_pnl = Decimal("0")
        fk100 = ""
        nk100 = ""
        tr_cont = ""
        for _ in range(10):
            params = {
                "CANO": cano,
                "ACNT_PRDT_CD": prdt,
                "SORT_DVSN": "00",
                "INQR_STRT_DT": start_date.strftime("%Y%m%d"),
                "INQR_END_DT": end_date.strftime("%Y%m%d"),
                "CBLC_DVSN": "00",
                "PDNO": "",
                "CTX_AREA_FK100": fk100,
                "CTX_AREA_NK100": nk100,
            }
            headers = await self._headers(TR_ID_PERIOD_TRADE_PROFIT)
            if tr_cont:
                headers["tr_cont"] = tr_cont
            resp = await self._client.get(url, headers=headers, params=params)
            if resp.status_code == 401:
                self._token = None
                self._cache.clear()
                resp = await self._client.get(
                    url,
                    headers=await self._headers(TR_ID_PERIOD_TRADE_PROFIT),
                    params=params,
                )
            if resp.status_code != 200:
                raise KISApiError(str(resp.status_code), resp.text)
            data = resp.json()
            if data.get("rt_cd") != "0":
                raise KISApiError(data.get("msg_cd", "?"), data.get("msg1", "unknown error"))
            for r in data.get("output1") or []:
                rows.append(_row_to_pnl(r))
            summary = data.get("output2") or {}
            total_buy = to_decimal(summary.get("buy_amt_smtl", "0"))
            total_sell = to_decimal(summary.get("sll_amt_smtl", "0"))
            total_pnl = to_decimal(summary.get("rlzt_pfls_smtl", "0"))
            next_cont = resp.headers.get("tr_cont", "").upper()
            if next_cont not in {"M", "F"}:
                break
            tr_cont = "N"
            fk100 = data.get("ctx_area_fk100", "") or ""
            nk100 = data.get("ctx_area_nk100", "") or ""
        return RealizedPnLSummary(
            rows=tuple(rows),
            total_buy_amount=total_buy,
            total_sell_amount=total_sell,
            total_realized_pnl=total_pnl,
        )


def _row_to_order(row: dict[str, Any]) -> Order:
    ord_qty = to_decimal(row.get("ord_qty", "0"))
    tot_ccld_qty = to_decimal(row.get("tot_ccld_qty", "0"))
    cncl_yn = (row.get("cncl_yn") or "N").upper()
    rfus_yn = (row.get("rfus_yn") or row.get("rjct_yn") or "N").upper()
    if rfus_yn == "Y":
        status = OrderStatus.REJECTED
    elif cncl_yn == "Y":
        status = OrderStatus.CANCELLED
    elif tot_ccld_qty >= ord_qty and ord_qty > 0:
        status = OrderStatus.FILLED
    elif tot_ccld_qty > 0:
        status = OrderStatus.PARTIAL
    else:
        status = OrderStatus.PENDING
    side = Side.SELL if str(row.get("sll_buy_dvsn_cd", "")) == "01" else Side.BUY
    kind = (
        OrderKind.MARKET
        if str(row.get("ord_dvsn_cd", row.get("ord_dvsn", ""))) == _ORD_DVSN_MARKET
        else OrderKind.LIMIT
    )
    ord_unpr = to_decimal(row.get("ord_unpr", "0"))
    avg_fill_raw = row.get("avg_prvs") or row.get("avg_prvs_pric") or "0"
    avg_fill = to_decimal(avg_fill_raw)
    return Order(
        order_id=OrderId(str(row["odno"])),
        symbol=Symbol(str(row["pdno"])),
        side=side,
        kind=kind,
        quantity=ord_qty,
        price=ord_unpr if kind is OrderKind.LIMIT else None,
        status=status,
        filled_quantity=tot_ccld_qty,
        avg_fill_price=avg_fill if tot_ccld_qty > 0 else None,
    )


def _row_to_fill(row: dict[str, Any]) -> Fill:
    side = Side.SELL if str(row.get("sll_buy_dvsn_cd", "")) == "01" else Side.BUY
    qty = to_decimal(row.get("tot_ccld_qty") or row.get("ccld_qty") or "0")
    price = to_decimal(
        row.get("avg_prvs") or row.get("avg_prvs_pric") or row.get("ccld_unpr") or "0"
    )
    ord_dt = str(row.get("ord_dt", ""))
    ord_tmd = str(row.get("ord_tmd") or row.get("ccld_tmd") or "")
    if ord_dt and ord_tmd and len(ord_dt) == 8 and len(ord_tmd) >= 6:
        fill_time = datetime.strptime(
            f"{ord_dt}{ord_tmd[:6]}", "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone(timedelta(hours=9)))
    else:
        fill_time = datetime.now(timezone(timedelta(hours=9)))
    return Fill(
        symbol=Symbol(str(row.get("pdno", ""))),
        side=side,
        quantity=qty,
        fill_price=price,
        fill_time=fill_time,
        fees=Decimal("0"),
        odno=OrderId(str(row.get("odno", ""))),
    )


def _row_to_pnl(row: dict[str, Any]) -> RealizedPnLRow:
    qty = to_decimal(row.get("trad_qty") or row.get("sll_qty") or "0")
    buy_amt = to_decimal(row.get("buy_amt") or "0")
    sell_amt = to_decimal(row.get("sll_amt") or "0")
    pnl = to_decimal(row.get("rlzt_pfls") or row.get("pfls_amt") or "0")
    ret_pct = to_decimal(row.get("pfls_rt") or row.get("pftrt") or "0")
    return RealizedPnLRow(
        symbol=Symbol(str(row.get("pdno", ""))),
        quantity=qty,
        buy_amount=buy_amt,
        sell_amount=sell_amt,
        realized_pnl=pnl,
        return_pct=ret_pct,
    )

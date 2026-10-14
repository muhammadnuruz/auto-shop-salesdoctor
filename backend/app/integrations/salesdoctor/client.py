"""Async Sales Doctor API client.

* One endpoint, JSON body ``{"method", "auth", "params" | "data"}``; errors arrive with HTTP 200.
* A browser-like User-Agent is mandatory (default python UA gets HTTP 403).
* Token lives in Redis; ``login`` invalidates the previous token, so re-login happens under an
  in-process lock plus a Redis distributed lock, and only if nobody else refreshed the token.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar

import httpx
import orjson
from pydantic import BaseModel, SecretStr

from app.core.logging import get_logger
from app.integrations.salesdoctor.errors import (
    SDAuthError,
    SDError,
    SDIncompleteFetchError,
    SDOutcomeUnknownError,
    SDTransportError,
    _as_int,
    parse_row_errors,
    raise_for_payload,
)
from app.integrations.salesdoctor.schemas import (
    Agent,
    BalanceItem,
    Brand,
    Client,
    Order,
    OrderIn,
    OrderStatusIn,
    PaymentType,
    PriceRow,
    PriceType,
    Product,
    ProductCategory,
    SetItemResult,
    SetResult,
    StockWarehouse,
    TradeDirection,
    Unit,
    Warehouse,
)
from app.integrations.salesdoctor.token_store import LoginLockTimeout, SDCredentials, TokenStore

log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)
Json = dict[str, Any]
SleepFn = Callable[[float], Awaitable[None]]

DEFAULT_PAGE_LIMIT = 500
DEFAULT_MAX_PAGES = 1000
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_RETRY_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


class SalesDoctorClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token_store: TokenStore,
        *,
        base_url: str,
        login: str,
        password: SecretStr,
        user_agent: str = "Mozilla/5.0 (compatible; AutoShop/1.0)",
        filial_id: int | None = None,
        timeout: float = 30.0,
        login_timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 10.0,
        retry_after_max: float = 30.0,
        max_pages: int = DEFAULT_MAX_PAGES,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._http = http
        self._store = token_store
        self._base_url = base_url
        self._login = login
        self._password = password
        self._user_agent = user_agent
        self._filial_id = filial_id
        self._timeout = timeout
        self._login_timeout = login_timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._retry_after_max = retry_after_max
        self._max_pages = max_pages
        self._sleep = sleep
        self._auth_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"SalesDoctorClient(base_url={self._base_url!r})"

    # ------------------------------------------------------------------ auth

    async def _login_request(self) -> SDCredentials:
        """Single login attempt (never retried) bounded by ``login_timeout`` overall."""
        body: Json = {
            "method": "login",
            "auth": {"login": self._login, "password": self._password.get_secret_value()},
        }
        try:
            async with asyncio.timeout(self._login_timeout):
                payload = await self._post(
                    body, method="login", retry=False, request_timeout=self._login_timeout
                )
        except TimeoutError:
            raise SDTransportError("login timed out", method="login", retryable=True) from None
        raise_for_payload("login", payload)
        result = payload.get("result")
        if not isinstance(result, Mapping) or "token" not in result:
            raise SDError("login response has no token", method="login")
        return SDCredentials(user_id=str(result["userId"]), token=str(result["token"]))

    @staticmethod
    def _usable(current: SDCredentials | None, failed: SDCredentials | None) -> bool:
        return current is not None and (failed is None or current.token != failed.token)

    async def _credentials(self, *, failed: SDCredentials | None = None) -> SDCredentials:
        """Return valid credentials, logging in only if needed.

        ``failed`` is the credential that just got a 401: a stored token that differs from it was
        refreshed by someone else and is used as-is.
        """
        current = await self._store.get()
        if current is not None and self._usable(current, failed):
            return current

        async with self._auth_lock:
            current = await self._store.get()
            if current is not None and self._usable(current, failed):
                return current
            try:
                lock = await self._store.acquire_lock()
            except LoginLockTimeout:
                current = await self._store.get()
                if current is not None and self._usable(current, failed):
                    return current
                raise SDTransportError(
                    "timed out waiting for SD login lock", method="login", retryable=True
                ) from None
            try:
                current = await self._store.get()
                if current is not None and self._usable(current, failed):
                    return current
                log.info("sd_login")
                creds = await self._login_request()
                if await self._store.owns(lock):
                    await self._store.set(creds)
                else:
                    # Lock expired mid-login: another process may have logged in since, so do not
                    # clobber its token. Our token is still used for this call.
                    log.warning("sd_login_lock_lost")
                return creds
            finally:
                await self._store.release(lock)

    # ------------------------------------------------------------------ transport

    def _backoff(self, attempt: int) -> float:
        delay: float = min(self._backoff_max, self._backoff_base * (2**attempt))
        return delay / 2 + random.uniform(0, delay / 2)

    def _retry_after(self, resp: httpx.Response) -> float | None:
        raw = resp.headers.get("retry-after")
        if raw is None:
            return None
        try:
            return min(self._retry_after_max, max(0.0, float(raw)))
        except ValueError:
            return None

    async def _post(
        self,
        body: Json,
        *,
        method: str,
        retry: bool,
        request_timeout: float | None = None,
    ) -> Json:
        """POST with retries.

        Reads retry on transport errors, timeouts, 429 and 5xx. Writes (set*) retry only when the
        request provably never reached the server (connect errors, pool timeouts, 429); any other
        failure raises ``SDOutcomeUnknownError`` so the caller can reconcile first.
        """
        write = method.startswith("set")
        attempts = (self._max_retries if retry else 0) + 1
        last: SDTransportError | None = None
        next_delay: float | None = None
        for attempt in range(attempts):
            if attempt:
                await self._sleep(
                    next_delay if next_delay is not None else self._backoff(attempt - 1)
                )
                next_delay = None
            started = time.perf_counter()
            try:
                resp = await self._http.post(
                    self._base_url,
                    content=orjson.dumps(body),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": self._user_agent,
                    },
                    timeout=request_timeout if request_timeout is not None else self._timeout,
                )
            except httpx.HTTPError as exc:
                name = type(exc).__name__
                if write and not isinstance(exc, _NOT_SENT):
                    raise SDOutcomeUnknownError(
                        f"{name} during write; outcome unknown, reconcile before resending",
                        method=method,
                    ) from None
                last = SDTransportError(f"{name} while calling SD", method=method)
                log.warning("sd_transport_error", method=method, attempt=attempt + 1, error=name)
                continue
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            log.debug("sd_call", method=method, status=resp.status_code, ms=elapsed_ms)

            code = resp.status_code
            if code == 429 or (code in _RETRY_HTTP_STATUSES and not write):
                last = SDTransportError(
                    f"HTTP {code} from SD", method=method, http_status=code, retryable=True
                )
                next_delay = self._retry_after(resp) if code == 429 else None
                log.warning("sd_http_error", method=method, status=code, attempt=attempt + 1)
                continue
            if code in _RETRY_HTTP_STATUSES:  # 5xx on a write
                raise SDOutcomeUnknownError(
                    f"HTTP {code} during write; outcome unknown, reconcile before resending",
                    method=method,
                )
            if code == 401:
                raise SDAuthError("HTTP 401 from SD", method=method, code=401)
            if code != 200:
                hint = " (check User-Agent)" if code == 403 else ""
                raise SDTransportError(
                    f"HTTP {code} from SD{hint}",
                    method=method,
                    http_status=code,
                    retryable=False,
                )
            try:
                payload = orjson.loads(resp.content)
            except orjson.JSONDecodeError:
                if write:
                    raise SDOutcomeUnknownError(
                        "non-JSON body for a write; outcome unknown", method=method
                    ) from None
                raise SDTransportError(
                    "SD returned a non-JSON body", method=method, retryable=False
                ) from None
            if not isinstance(payload, dict):
                raise SDTransportError(
                    "SD returned an unexpected JSON shape", method=method, retryable=False
                )
            return payload
        raise last or SDTransportError("no attempt made", method=method)

    def _build_body(
        self,
        method: str,
        creds: SDCredentials | None,
        params: Mapping[str, Any] | None,
        data: Mapping[str, Any] | None,
    ) -> Json:
        body: Json = {"method": method}
        if creds is not None:
            body["auth"] = {"userId": creds.user_id, "token": creds.token}
        if self._filial_id is not None:
            body["filial"] = {"filial_id": self._filial_id}
        if params is not None:
            body["params"] = dict(params)
        if data is not None:
            body["data"] = dict(data)
        return body

    async def call(
        self,
        method: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        auth: bool = True,
        retry: bool = True,
    ) -> Json:
        """Call an SD method and return the full decoded response (status already checked).

        On 401 the token is refreshed once and the call repeated; a second 401 is raised.
        """
        creds = await self._credentials() if auth else None
        try:
            payload = await self._post(
                self._build_body(method, creds, params, data), method=method, retry=retry
            )
            raise_for_payload(method, payload)
            return payload
        except SDAuthError:
            if creds is None:
                raise
        creds = await self._credentials(failed=creds)
        payload = await self._post(
            self._build_body(method, creds, params, data), method=method, retry=retry
        )
        raise_for_payload(method, payload)
        return payload

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _rows(payload: Mapping[str, Any], key: str | Sequence[str]) -> list[Any]:
        result = payload.get("result")
        keys = [key] if isinstance(key, str) else list(key)
        if isinstance(result, Mapping):
            for k in keys:
                value = result.get(k)
                if isinstance(value, list):
                    return value
        elif isinstance(result, list):
            return result
        return []

    async def _list(
        self,
        method: str,
        key: str | Sequence[str],
        model: type[M],
        params: Mapping[str, Any] | None = None,
    ) -> list[M]:
        payload = await self.call(method, params=params)
        return [model.model_validate(r) for r in self._rows(payload, key)]

    async def iterate_raw(
        self,
        method: str,
        result_key: str,
        params: Mapping[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> AsyncIterator[Json]:
        """Yield rows page by page (pages start at 1).

        Stops when collected >= pagination.total, on an empty page, or on a short page when the
        server sends no pagination block.
        """
        base = dict(params or {})
        page = 1
        collected = 0
        total: int | None = None
        prev_first: str | None = None
        while True:
            if page > self._max_pages:
                raise SDError(f"pagination exceeded max_pages={self._max_pages}", method=method)
            payload = await self.call(method, params={**base, "page": page, "limit": limit})
            rows = self._rows(payload, result_key)
            pagination = payload.get("pagination")
            page_size = limit
            if isinstance(pagination, Mapping):
                total = _as_int(pagination.get("total"))
                server_limit = _as_int(pagination.get("limit"))
                if server_limit and server_limit > 0:
                    page_size = min(limit, server_limit)
            if not rows:
                if total is not None and collected < total:
                    log.error("sd_pagination_incomplete", method=method, page=page)
                    raise SDIncompleteFetchError(
                        f"empty page {page} after {collected} of {total} rows", method=method
                    )
                return
            first = orjson.dumps(rows[0], option=orjson.OPT_SORT_KEYS).decode()
            if prev_first is not None and first == prev_first:
                log.error("sd_pagination_ignored", method=method, page=page)
                raise SDIncompleteFetchError(
                    f"server ignored page={page} (repeated page); got {collected} rows",
                    method=method,
                )
            prev_first = first
            for row in rows:
                yield row
            collected += len(rows)
            if total is not None:
                if collected >= total:
                    return
            elif len(rows) < page_size:
                return
            page += 1

    async def iterate(
        self,
        method: str,
        result_key: str,
        params: Mapping[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        *,
        model: type[M] | None = None,
    ) -> AsyncIterator[Any]:
        """Like ``iterate_raw`` but yields ``model`` instances when ``model`` is given."""
        async for row in self.iterate_raw(method, result_key, params, limit):
            yield model.model_validate(row) if model is not None else row

    async def fetch_all(
        self,
        method: str,
        result_key: str,
        params: Mapping[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        *,
        model: type[M] | None = None,
    ) -> list[Any]:
        return [row async for row in self.iterate(method, result_key, params, limit, model=model)]

    # ------------------------------------------------------------------ reference data

    async def get_raw_list(
        self,
        method: str,
        result_keys: str | Sequence[str],
        params: Mapping[str, Any] | None = None,
    ) -> list[Json]:
        """Unpaginated read returning raw rows (first matching result key wins)."""
        payload = await self.call(method, params=params)
        return [r for r in self._rows(payload, result_keys) if isinstance(r, dict)]

    async def get_warehouses(self) -> list[Warehouse]:
        return await self._list("getWarehouse", "warehouse", Warehouse)

    async def get_price_types(self) -> list[PriceType]:
        return await self._list("getPriceType", "priceType", PriceType)

    async def get_trade_directions(self) -> list[TradeDirection]:
        return await self._list("getTradeDirection", "tradeDirection", TradeDirection)

    async def get_payment_types(self) -> list[PaymentType]:
        # Live server returns the key "currency"; docs say "paymentType". Accept both.
        return await self._list("getPaymentType", ("currency", "paymentType"), PaymentType)

    async def get_agents(self) -> list[Agent]:
        return await self._list("getAgent", "agent", Agent)

    async def get_units(self) -> list[Unit]:
        return await self._list("getUnit", "unit", Unit)

    async def get_product_categories(self) -> list[ProductCategory]:
        return await self._list("getProductCategory", "productCategory", ProductCategory)

    async def get_brands(self) -> list[Brand]:
        return await self._list("getBrand", "brand", Brand)

    def iterate_products(
        self, filter_: Mapping[str, Any] | None = None, limit: int = DEFAULT_PAGE_LIMIT
    ) -> AsyncIterator[Product]:
        params = {"filter": dict(filter_)} if filter_ else None
        return self.iterate("getProduct", "product", params, limit, model=Product)

    def iterate_clients(
        self, filter_: Mapping[str, Any] | None = None, limit: int = DEFAULT_PAGE_LIMIT
    ) -> AsyncIterator[Client]:
        params = {"filter": dict(filter_)} if filter_ else None
        return self.iterate("getClient", "client", params, limit, model=Client)

    # ------------------------------------------------------------------ stock / price / balance

    async def get_stock(self, params: Mapping[str, Any] | None = None) -> list[StockWarehouse]:
        """All warehouses with nested products (unpaginated)."""
        return await self._list("getStock", "warehouse", StockWarehouse, params)

    async def get_prices(self, price_type_sd_id: str) -> list[tuple[str, float | None]]:
        """(product SD_id, price) pairs; ``result`` is a plain list without pagination."""
        params = {"priceType": {"SD_id": price_type_sd_id}}
        rows = await self._list("getPrice", "price", PriceRow, params)
        return [(r.product.sd_id or "", r.price) for r in rows if r.product.sd_id]

    async def get_balances(self, params: Mapping[str, Any] | None = None) -> list[BalanceItem]:
        """All balances (paginated on the server: default page 1000)."""
        rows = await self.fetch_all("getBalance", "balance", params, 1000, model=BalanceItem)
        return [r for r in rows if isinstance(r, BalanceItem)]

    # ------------------------------------------------------------------ orders

    @staticmethod
    def _order_filter(
        statuses: Sequence[int] | None,
        date_from: str | None,
        date_to: str | None,
        date_field: str,
        include: str,
        extra_filter: Mapping[str, Any] | None,
        allow_unbounded: bool,
    ) -> Json:
        if not allow_unbounded and not (date_from and date_to):
            raise ValueError(
                "getOrder needs both date_from and date_to (the server holds ~50k orders); "
                "pass allow_unbounded=True to override"
            )
        # include goes INSIDE filter (SD quirk). With no filter at all SD returns only status 1.
        flt: Json = {"include": include}
        if statuses is not None:
            flt["status"] = list(statuses)
        if date_from or date_to:
            window: Json = {}
            if date_from:
                window["from"] = date_from
            if date_to:
                window["to"] = date_to
            flt["period"] = {date_field: window}
        if extra_filter:
            flt.update(extra_filter)
        return flt

    async def iterate_orders(
        self,
        *,
        statuses: Sequence[int] | None = (1, 2, 3, 4, 5),
        date_from: str | None = None,
        date_to: str | None = None,
        date_field: str = "dateCreate",
        include: str = "all",
        extra_filter: Mapping[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        allow_unbounded: bool = False,
    ) -> AsyncIterator[Order]:
        """Stream orders page by page. A date window is required unless ``allow_unbounded``."""
        flt = self._order_filter(
            statuses, date_from, date_to, date_field, include, extra_filter, allow_unbounded
        )
        async for row in self.iterate_raw("getOrder", "order", {"filter": flt}, limit):
            yield Order.model_validate(row)

    async def get_orders(
        self,
        *,
        statuses: Sequence[int] | None = (1, 2, 3, 4, 5),
        date_from: str | None = None,
        date_to: str | None = None,
        date_field: str = "dateCreate",
        include: str = "all",
        extra_filter: Mapping[str, Any] | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        allow_unbounded: bool = False,
    ) -> list[Order]:
        """Fetch all orders in a date window (both bounds required unless ``allow_unbounded``)."""
        return [
            o
            async for o in self.iterate_orders(
                statuses=statuses,
                date_from=date_from,
                date_to=date_to,
                date_field=date_field,
                include=include,
                extra_filter=extra_filter,
                limit=limit,
                allow_unbounded=allow_unbounded,
            )
        ]

    @staticmethod
    def _dump(items: Sequence[BaseModel]) -> list[Json]:
        return [i.model_dump(by_alias=True, exclude_none=True, mode="json") for i in items]

    @staticmethod
    def _set_result(payload: Mapping[str, Any]) -> SetResult:
        result = payload.get("result")
        result = result if isinstance(result, Mapping) else {}
        raw_data = result.get("data")
        items: dict[str, list[SetItemResult]] = {}
        if isinstance(raw_data, Mapping):
            for entity, rows in raw_data.items():
                if isinstance(rows, list):
                    items[str(entity)] = [
                        SetItemResult.model_validate(r) for r in rows if isinstance(r, Mapping)
                    ]
        err = payload.get("error")
        row_errors = parse_row_errors(err.get("data")) if isinstance(err, Mapping) else []
        if not row_errors:
            row_errors = [r for r in parse_row_errors(raw_data) if r.messages]
        return SetResult(
            completed=_as_int(result.get("completed")) or 0,
            error=_as_int(result.get("error")) or 0,
            items=items,
            row_errors=row_errors,
        )

    async def set_orders(self, orders: Sequence[OrderIn], *, retry: bool = True) -> SetResult:
        """Create/update orders. Idempotent per ``code_1C`` (same code updates the same order)."""
        payload = await self.call("setOrder", data={"order": self._dump(orders)}, retry=retry)
        return self._set_result(payload)

    async def set_order_status(
        self, changes: Sequence[OrderStatusIn], *, retry: bool = True
    ) -> SetResult:
        payload = await self.call("setStatus", data={"order": self._dump(changes)}, retry=retry)
        return self._set_result(payload)

    async def delete_orders(
        self,
        refs: Sequence[Mapping[str, Any]],
        *,
        retry: bool = True,
    ) -> SetResult:
        """``refs``: dicts with CS_id / SD_id / code_1C."""
        rows = [
            {
                "CS_id": r.get("CS_id", ""),
                "SD_id": r.get("SD_id", ""),
                "code_1C": r.get("code_1C", ""),
            }
            for r in refs
        ]
        payload = await self.call("setDeletedOrder", data={"deletedOrder": rows}, retry=retry)
        return self._set_result(payload)

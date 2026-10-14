"""Review-driven hardening tests (lock handling, unsafe write retries, pagination, ...)."""

import asyncio
import json
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.integrations.salesdoctor import (
    SalesDoctorClient,
    SDAuthError,
    SDCredentials,
    SDError,
    SDIncompleteFetchError,
    SDOutcomeUnknownError,
    SDTransportError,
    TokenStore,
)
from app.integrations.salesdoctor.errors import raise_for_payload
from app.integrations.salesdoctor.schemas import EntityID, OrderIn, OrderProductIn
from app.integrations.salesdoctor.token_store import LoginLockTimeout
from tests.conftest import BASE_URL, STRONG_SECRET, login_ok, ok, sd_error


@pytest.fixture
def route(mock_sd: respx.MockRouter) -> respx.Route:
    return mock_sd.post(BASE_URL)


def methods(route: respx.Route) -> list[str]:
    return [json.loads(c.request.content)["method"] for c in route.calls]


def make_order(code: str = "WEB-1") -> OrderIn:
    return OrderIn(
        code_1C=code,
        client=EntityID(SD_id="c"),
        agent=EntityID(SD_id="a"),
        priceType=EntityID(SD_id="p"),
        warehouse=EntityID(SD_id="w"),
        trade=EntityID(SD_id=1),
        orderProducts=[OrderProductIn(product=EntityID(SD_id="x"), quantity=1, price=1)],
    )


async def seed(store: TokenStore, token: str = "t") -> None:
    await store.set(SDCredentials("d0_1", token))


# --- 1/2/3: login lock handling ---------------------------------------------------------------


async def test_login_is_not_retried_and_failure_is_transport(
    sd: SalesDoctorClient, route: respx.Route, sleeps: list[float]
) -> None:
    route.mock(return_value=httpx.Response(503))
    with pytest.raises(SDTransportError):
        await sd.get_warehouses()
    assert methods(route) == ["login"] and sleeps == []


async def test_login_has_overall_timeout(
    store: TokenStore, route: respx.Route, redis: fakeredis.FakeAsyncRedis
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=login_ok("x"))

    route.mock(side_effect=slow)
    async with httpx.AsyncClient() as http:
        c = SalesDoctorClient(
            http, store, base_url=BASE_URL, login="u", password=SecretStr("p"), login_timeout=0.05
        )
        with pytest.raises(SDTransportError, match="timed out"):
            await c.get_warehouses()
    assert await redis.exists(store.lock_key) == 0  # lock released


async def test_expired_lock_during_login_keeps_creds_and_does_not_write(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "login":
            await redis.delete(store.lock_key)  # simulate TTL expiry mid-login
            return httpx.Response(200, json=login_ok("fresh"))
        assert body["auth"]["token"] == "fresh"
        return httpx.Response(200, json=ok({"warehouse": []}))

    route.mock(side_effect=handler)
    assert await sd.get_warehouses() == []  # no LockNotOwnedError leaks out
    assert await store.get() is None  # did not clobber the store without owning the lock


async def test_lock_wait_timeout_rereads_store_then_uses_fresh_token(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = SDCredentials("d0_1", "old")
    fresh = SDCredentials("d0_1", "fresh")
    reads = iter([old, old, old, fresh])

    async def fake_get() -> SDCredentials:
        return next(reads)

    async def fake_acquire() -> Any:
        raise LoginLockTimeout

    monkeypatch.setattr(store, "get", fake_get)
    monkeypatch.setattr(store, "acquire_lock", fake_acquire)

    def handler(request: httpx.Request) -> httpx.Response:
        tok = json.loads(request.content)["auth"]["token"]
        if tok == "fresh":
            return httpx.Response(200, json=ok({"warehouse": []}))
        return httpx.Response(200, json=sd_error(401, "Invalid token"))

    route.mock(side_effect=handler)
    assert await sd.get_warehouses() == []
    assert "login" not in methods(route)


async def test_lock_wait_timeout_without_fresh_token_raises_retryable(
    redis: fakeredis.FakeAsyncRedis, route: respx.Route
) -> None:
    store = TokenStore(redis, lock_timeout=0.3)
    await redis.set(store.lock_key, "someone-else", px=5000)
    async with httpx.AsyncClient() as http:
        c = SalesDoctorClient(http, store, base_url=BASE_URL, login="u", password=SecretStr("p"))
        with pytest.raises(SDTransportError) as ei:
            await c.get_warehouses()
    assert ei.value.retryable and not route.called


# --- 4: unsafe write retries ------------------------------------------------------------------


@pytest.mark.parametrize(
    "effect",
    [httpx.ReadTimeout("x"), httpx.RemoteProtocolError("x"), httpx.Response(502)],
)
async def test_write_ambiguous_failure_is_outcome_unknown_no_retry(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, effect: Any
) -> None:
    await seed(store)
    route.side_effect = [effect]
    with pytest.raises(SDOutcomeUnknownError):
        await sd.set_orders([make_order()])
    assert len(route.calls) == 1


async def test_write_retries_when_provably_not_sent(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, sleeps: list[float]
) -> None:
    await seed(store)
    route.side_effect = [
        httpx.ConnectError("x"),
        httpx.ConnectTimeout("x"),
        httpx.Response(200, json=ok({"completed": 1, "error": 0, "data": {}})),
    ]
    res = await sd.set_orders([make_order()])
    assert res.ok and len(route.calls) == 3 and len(sleeps) == 2


def test_order_code_1c_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        make_order("")


# --- 5: pagination hardening ------------------------------------------------------------------


async def test_pagination_string_total_and_server_limit(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store)
    pages = {
        1: ok({"p": [{"SD_id": "a"}, {"SD_id": "b"}]}, pagination={"total": "3", "limit": "2"}),
        2: ok({"p": [{"SD_id": "c"}]}, pagination={"total": "3", "limit": "2"}),
    }
    route.mock(
        side_effect=lambda r: httpx.Response(
            200, json=pages[json.loads(r.content)["params"]["page"]]
        )
    )
    rows = await sd.fetch_all("m", "p", limit=500)
    assert [r["SD_id"] for r in rows] == ["a", "b", "c"] and len(route.calls) == 2


async def test_pagination_short_page_uses_server_limit(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store)
    route.mock(
        return_value=httpx.Response(
            200, json=ok({"p": [{"SD_id": "a"}, {"SD_id": "b"}]}, pagination={"limit": 2})
        )
    )
    # page 1 is full per server limit and total unknown -> continues; identical page 2 -> error
    with pytest.raises(SDIncompleteFetchError):
        await sd.fetch_all("m", "p", limit=500)


async def test_pagination_detects_ignored_page_param(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store)
    same = ok({"p": [{"SD_id": "a"}, {"SD_id": "b"}]}, pagination={"total": 100, "limit": 2})
    route.mock(return_value=httpx.Response(200, json=same))
    with pytest.raises(SDIncompleteFetchError, match="ignored"):
        await sd.fetch_all("m", "p", limit=2)
    assert len(route.calls) == 2


async def test_pagination_max_pages_guard(route: respx.Route, store: TokenStore) -> None:
    await seed(store)

    def handler(r: httpx.Request) -> httpx.Response:
        page = json.loads(r.content)["params"]["page"]
        return httpx.Response(
            200, json=ok({"p": [{"SD_id": f"r{page}"}]}, pagination={"total": 10**6, "limit": 1})
        )

    route.mock(side_effect=handler)
    async with httpx.AsyncClient() as http:
        c = SalesDoctorClient(
            http, store, base_url=BASE_URL, login="u", password=SecretStr("p"), max_pages=3
        )
        with pytest.raises(SDError, match="max_pages"):
            await c.fetch_all("m", "p", limit=1)
    assert len(route.calls) == 3


# --- 6: partial failures in status:true responses ---------------------------------------------


async def test_set_result_surfaces_row_errors(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store)
    payload = {
        "status": True,
        "result": {"completed": 1, "error": 1, "data": {}},
        "error": {
            "code": 400,
            "message": "Error",
            "data": {"order": [{"code_1C": "WEB-2", "index": 1, "errors": ["Bad product"]}]},
        },
    }
    route.mock(return_value=httpx.Response(200, json=payload))
    res = await sd.set_orders([make_order("WEB-1"), make_order("WEB-2")])
    assert not res.ok and res.error == 1
    assert res.row_errors[0].messages == ["Bad product"] and res.row_errors[0].index == 1


async def test_set_result_error_count_without_rows_not_ok(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store)
    route.mock(return_value=httpx.Response(200, json=ok({"completed": 0, "error": 2, "data": {}})))
    assert not (await sd.set_orders([make_order()])).ok


# --- 7: error code parsing --------------------------------------------------------------------


def test_numeric_string_codes() -> None:
    with pytest.raises(SDAuthError):
        raise_for_payload("m", {"status": False, "error": {"code": "401", "message": "x"}})


async def test_http_401_is_auth_error_and_relogins(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store, "old")
    route.side_effect = [
        httpx.Response(401),
        httpx.Response(200, json=login_ok("new")),
        httpx.Response(200, json=ok({"warehouse": []})),
    ]
    await sd.get_warehouses()
    assert methods(route) == ["getWarehouse", "login", "getWarehouse"]


# --- 8: redis down ----------------------------------------------------------------------------


async def test_redis_failure_wrapped_as_transport_error(route: respx.Route) -> None:
    from redis.asyncio import Redis

    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    async with httpx.AsyncClient() as http:
        c = SalesDoctorClient(
            http, TokenStore(dead), base_url=BASE_URL, login="u", password=SecretStr("p")
        )
        with pytest.raises(SDTransportError, match="token store"):
            await c.get_warehouses()
    await dead.aclose()
    assert not route.called


# --- 10: orders date window -------------------------------------------------------------------


async def test_get_orders_requires_window(sd: SalesDoctorClient) -> None:
    with pytest.raises(ValueError, match="date_from and date_to"):
        await sd.get_orders()
    with pytest.raises(ValueError):
        await sd.get_orders(date_from="2026-10-01 00:00:00")


async def test_get_orders_allow_unbounded_and_iterate(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store)
    route.mock(
        return_value=httpx.Response(
            200, json=ok({"order": [{"SD_id": "o1", "status": 1}]}, pagination={"total": 1})
        )
    )
    assert len(await sd.get_orders(allow_unbounded=True)) == 1
    got = [
        o
        async for o in sd.iterate_orders(
            date_from="2026-10-01 00:00:00", date_to="2026-10-02 00:00:00"
        )
    ]
    assert got[0].SD_id == "o1"
    assert json.loads(route.calls[0].request.content)["params"]["filter"]["include"] == "all"


# --- 15/16/17: retry-after, namespacing -------------------------------------------------------


async def test_retry_after_honored_and_capped(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, sleeps: list[float]
) -> None:
    await seed(store)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(429, headers={"Retry-After": "9999"}),
        httpx.Response(200, json=ok({"warehouse": []})),
    ]
    await sd.get_warehouses()
    assert sleeps == [7.0, 30.0]


async def test_token_key_namespaced_per_account(redis: fakeredis.FakeAsyncRedis) -> None:
    a = TokenStore.for_account(redis, base_url="https://a/", login="u")
    b = TokenStore.for_account(redis, base_url="https://a/", login="v")
    assert a.key != b.key and a.lock_key != b.lock_key and a.key.startswith("sd:auth:")
    await a.set(SDCredentials("1", "tok"))
    assert await b.get() is None


# --- 14: settings validation ------------------------------------------------------------------


def test_settings_require_sd_credentials_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(ENV="production")
    Settings(
        ENV="production",
        SD_LOGIN="u",
        SD_PASSWORD=SecretStr("p"),
        APP_SECRET_KEY=SecretStr(STRONG_SECRET),
        ALLOWED_ORIGINS="https://shop.example.uz",
    )


def test_settings_lock_timeout_must_exceed_login_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(SD_LOGIN_TIMEOUT=30, SD_LOCK_TIMEOUT=30)


async def test_pagination_first_page_empty_but_total_positive_is_incomplete(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await seed(store)
    route.mock(return_value=httpx.Response(200, json=ok({"p": []}, pagination={"total": 5})))
    with pytest.raises(SDIncompleteFetchError):
        await sd.fetch_all("m", "p")

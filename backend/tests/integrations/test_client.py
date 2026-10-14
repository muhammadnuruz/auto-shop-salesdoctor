import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.integrations.salesdoctor import (
    SalesDoctorClient,
    SDAuthError,
    SDIncompleteFetchError,
    SDMethodError,
    SDTimeWindowError,
    SDTransportError,
    SDValidationError,
    TokenStore,
)
from app.integrations.salesdoctor.schemas import EntityID, OrderIn, OrderProductIn
from tests.conftest import BASE_URL, UA, login_ok, ok, sd_error


def bodies(route: respx.Route) -> list[dict[str, Any]]:
    return [json.loads(c.request.content) for c in route.calls]


def methods(route: respx.Route) -> list[str]:
    return [b["method"] for b in bodies(route)]


@pytest.fixture
def route(mock_sd: respx.MockRouter) -> respx.Route:
    return mock_sd.post(BASE_URL)


# ------------------------------------------------------------------ auth / headers


async def test_user_agent_and_login_cached_in_redis(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, redis: Any
) -> None:
    route.side_effect = [
        httpx.Response(200, json=login_ok("a" * 32)),
        httpx.Response(200, json=ok({"warehouse": []})),
        httpx.Response(200, json=ok({"warehouse": []})),
    ]
    await sd.get_warehouses()
    await sd.get_warehouses()

    assert methods(route) == ["login", "getWarehouse", "getWarehouse"]
    assert all(c.request.headers["user-agent"] == UA for c in route.calls)
    login_body = bodies(route)[0]
    assert login_body["auth"] == {"login": "user", "password": "s3cret-pass"}
    assert bodies(route)[1]["auth"] == {"userId": "d0_92", "token": "a" * 32}
    creds = await store.get()
    assert creds is not None and creds.token == "a" * 32
    assert "a" * 32 in await redis.get("sd:auth")


async def test_existing_token_in_redis_skips_login(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "t" * 32))
    route.mock(return_value=httpx.Response(200, json=ok({"warehouse": []})))
    await sd.get_warehouses()
    assert methods(route) == ["getWarehouse"]


async def test_401_relogin_once_and_retry(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "old"))
    route.side_effect = [
        httpx.Response(200, json=sd_error(401, "Invalid token")),
        httpx.Response(200, json=login_ok("new")),
        httpx.Response(
            200, json=ok({"warehouse": [{"SD_id": "d0_1", "name": "W", "active": "Y"}]})
        ),
    ]
    result = await sd.get_warehouses()
    assert methods(route) == ["getWarehouse", "login", "getWarehouse"]
    assert bodies(route)[2]["auth"]["token"] == "new"
    assert result[0].name == "W" and result[0].active is True


async def test_second_401_raises_without_loop(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "old"))
    route.side_effect = [
        httpx.Response(200, json=sd_error(401, "Invalid token")),
        httpx.Response(200, json=login_ok("new")),
        httpx.Response(200, json=sd_error(401, "Invalid token")),
    ]
    with pytest.raises(SDAuthError):
        await sd.get_warehouses()
    assert methods(route) == ["getWarehouse", "login", "getWarehouse"]


async def test_concurrent_401s_trigger_exactly_one_login(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "old"))

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "login":
            return httpx.Response(200, json=login_ok("fresh"))
        if body["auth"]["token"] == "fresh":
            return httpx.Response(200, json=ok({"warehouse": []}))
        return httpx.Response(200, json=sd_error(401, "Invalid token"))

    route.mock(side_effect=handler)
    results = await asyncio.gather(*(sd.get_warehouses() for _ in range(10)))
    assert all(r == [] for r in results)
    assert methods(route).count("login") == 1


async def test_cross_process_lock_reuses_token_refreshed_by_other(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, redis: Any
) -> None:
    """A second client (other process) sharing Redis must not log in again."""
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "old"))
    other = SalesDoctorClient(
        sd._http,
        TokenStore(redis, lock_timeout=5),
        base_url=BASE_URL,
        login="user",
        password=SecretStr("s3cret-pass"),
        user_agent=UA,
        sleep=sd._sleep,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "login":
            return httpx.Response(200, json=login_ok("fresh"))
        if body["auth"]["token"] == "fresh":
            return httpx.Response(200, json=ok({"warehouse": []}))
        return httpx.Response(200, json=sd_error(401, "Invalid token"))

    route.mock(side_effect=handler)
    await asyncio.gather(sd.get_warehouses(), other.get_warehouses())
    assert methods(route).count("login") == 1


async def test_secrets_not_in_reprs_or_errors(sd: SalesDoctorClient, route: respx.Route) -> None:
    route.mock(
        return_value=httpx.Response(200, json=sd_error(401, "Invalid token")),
    )
    with pytest.raises(SDAuthError) as ei:
        await sd.call("login", auth=False)
    text = repr(sd) + str(ei.value) + repr(ei.value)
    assert "s3cret-pass" not in text


# ------------------------------------------------------------------ SD errors


async def test_402_invalid_method(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "t"))
    route.mock(return_value=httpx.Response(200, json=sd_error(402, "Invalid method")))
    with pytest.raises(SDMethodError):
        await sd.call("nope")
    assert len(route.calls) == 1  # not retried


async def test_validation_error_items(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "t"))
    payload = {
        "status": False,
        "result": {"completed": 0, "error": 1, "data": {}},
        "error": {
            "code": 400,
            "message": "Error",
            "data": {
                "order": [
                    {
                        "SD_id": "",
                        "code_1C": "WEB-1",
                        "name": None,
                        "index": 0,
                        "errors": ["Agent is not found"],
                    }
                ]
            },
        },
    }
    route.mock(return_value=httpx.Response(200, json=payload))
    order = OrderIn(
        code_1C="WEB-1",
        client=EntityID(SD_id="c"),
        agent=EntityID(SD_id="bad"),
        priceType=EntityID(SD_id="p"),
        warehouse=EntityID(SD_id="w"),
        trade=EntityID(SD_id=1),
        orderProducts=[OrderProductIn(product=EntityID(SD_id="x"), quantity=1, price=10)],
    )
    with pytest.raises(SDValidationError) as ei:
        await sd.set_orders([order])
    assert ei.value.items[0].messages == ["Agent is not found"]
    assert ei.value.items[0].ids["code_1C"] == "WEB-1"


async def test_time_window_string_error(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "t"))
    route.mock(
        return_value=httpx.Response(
            200, json={"status": False, "error": "this request allowed time is from 20:00 to 08:00"}
        )
    )
    with pytest.raises(SDTimeWindowError):
        await sd.call("getStockForDate")


# ------------------------------------------------------------------ transport / retries


async def _seed(store: TokenStore) -> None:
    from app.integrations.salesdoctor import SDCredentials

    await store.set(SDCredentials("d0_1", "t"))


async def test_transport_retry_with_backoff_then_success(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, sleeps: list[float]
) -> None:
    await _seed(store)
    route.side_effect = [
        httpx.ConnectError("boom"),
        httpx.Response(502),
        httpx.ReadTimeout("slow"),
        httpx.Response(200, json=ok({"warehouse": []})),
    ]
    assert await sd.get_warehouses() == []
    assert len(route.calls) == 4
    assert len(sleeps) == 3
    # exponential growth within jitter bounds (base 0.5 -> 0.5, 1, 2 ; delay/2..delay)
    for i, s in enumerate(sleeps):
        d = 0.5 * 2**i
        assert d / 2 <= s <= d


async def test_transport_retries_exhausted(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore, sleeps: list[float]
) -> None:
    await _seed(store)
    route.mock(return_value=httpx.Response(503))
    with pytest.raises(SDTransportError) as ei:
        await sd.get_warehouses()
    assert ei.value.http_status == 503
    assert len(route.calls) == 4  # 1 + max_retries
    assert len(sleeps) == 3


async def test_retry_false_single_attempt(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(return_value=httpx.Response(500))
    with pytest.raises(SDTransportError):
        await sd.call("getWarehouse", retry=False)
    assert len(route.calls) == 1


async def test_403_not_retried(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(return_value=httpx.Response(403))
    with pytest.raises(SDTransportError) as ei:
        await sd.get_warehouses()
    assert ei.value.http_status == 403 and not ei.value.retryable
    assert len(route.calls) == 1


async def test_non_json_body(sd: SalesDoctorClient, route: respx.Route, store: TokenStore) -> None:
    await _seed(store)
    route.mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(SDTransportError):
        await sd.get_warehouses()


# ------------------------------------------------------------------ pagination


def page_handler(total: int, limit: int, seen: list[int], key: str = "product") -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)["params"]
        page = params["page"]
        seen.append(page)
        start = (page - 1) * limit
        rows = [
            {"SD_id": f"p{i}", "name": f"n{i}"} for i in range(start, min(start + limit, total))
        ]
        return httpx.Response(
            200,
            json=ok({key: rows}, pagination={"limit": limit, "total": total, "page": page}),
        )

    return handler


async def test_pagination_across_pages_stops_at_total(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    seen: list[int] = []
    route.mock(side_effect=page_handler(5, 2, seen))
    rows = await sd.fetch_all("getProduct", "product", limit=2)
    assert [r["SD_id"] for r in rows] == ["p0", "p1", "p2", "p3", "p4"]
    assert seen == [1, 2, 3]  # no extra empty-page request


async def test_pagination_exact_multiple_no_extra_call(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    seen: list[int] = []
    route.mock(side_effect=page_handler(4, 2, seen))
    rows = await sd.fetch_all("getProduct", "product", limit=2)
    assert len(rows) == 4 and seen == [1, 2]


async def test_pagination_stops_on_empty_page(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    pages = iter(
        [
            ok({"product": [{"SD_id": "a"}, {"SD_id": "b"}]}, pagination={"total": 99}),
            ok({"product": []}, pagination={"total": 99}),
        ]
    )
    route.mock(side_effect=lambda r: httpx.Response(200, json=next(pages)))
    with pytest.raises(SDIncompleteFetchError, match="empty page"):
        await sd.fetch_all("getProduct", "product", limit=2)
    assert len(route.calls) == 2


async def test_pagination_without_block_stops_on_short_page(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(return_value=httpx.Response(200, json=ok({"product": [{"SD_id": "a"}]})))
    assert len(await sd.fetch_all("getProduct", "product", limit=2)) == 1
    assert len(route.calls) == 1


async def test_iterate_products_models_and_filter(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    row = {
        "SD_id": "d0_1",
        "name": "Brush",
        "active": "Y",
        "barCode": "123",
        "packQuantity": "12",
        "category": {"CS_id": "1", "SD_id": 1, "code_1C": None},
        "weirdNewField": 5,
    }
    route.mock(
        return_value=httpx.Response(200, json=ok({"product": [row]}, pagination={"total": 1}))
    )
    items = [p async for p in sd.iterate_products({"active": "Y"})]
    assert items[0].barCode == "123" and items[0].active is True and items[0].packQuantity == 12
    assert items[0].category is not None and items[0].category.sd_id == "1"
    assert items[0].model_extra == {"weirdNewField": 5}
    assert bodies(route)[0]["params"] == {"filter": {"active": "Y"}, "page": 1, "limit": 500}


# ------------------------------------------------------------------ typed endpoints


async def test_get_prices_plain_list(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(
        return_value=httpx.Response(
            200,
            json=ok(
                [
                    {
                        "product": {"CS_id": "d0_468", "SD_id": "d0_468", "code_1C": "PZ"},
                        "price": 64.6,
                    },
                    {"product": {"SD_id": "d0_2"}, "price": "10"},
                ]
            ),
        )
    )
    prices = await sd.get_prices("d0_3")
    assert prices == [("d0_468", 64.6), ("d0_2", 10.0)]
    assert bodies(route)[0]["params"] == {"priceType": {"SD_id": "d0_3"}}


async def test_get_payment_types_currency_key(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(
        return_value=httpx.Response(
            200, json=ok({"currency": [{"SD_id": "d0_1", "name": "Cash", "active": "Y"}]})
        )
    )
    (pt,) = await sd.get_payment_types()
    assert pt.name == "Cash" and pt.SD_id == "d0_1"


async def test_get_agents_user_list_or_object(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(
        return_value=httpx.Response(
            200,
            json=ok(
                {
                    "agent": [
                        {"SD_id": "a1", "name": "A", "user": [{"id": 1}, {"id": 2}]},
                        {"SD_id": "a2", "name": "B", "user": {"id": 3}},
                        {"SD_id": "a3", "name": "C", "user": None},
                    ]
                }
            ),
        )
    )
    a1, a2, a3 = await sd.get_agents()
    assert [u["id"] for u in a1.users] == [1, 2]
    assert [u["id"] for u in a2.users] == [3]
    assert a3.users == []


async def test_get_stock_nested(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(
        return_value=httpx.Response(
            200,
            json=ok(
                {
                    "warehouse": [
                        {
                            "SD_id": "d0_1",
                            "name": "Main",
                            "active": "Y",
                            "products": [{"SD_id": "d0_2784", "quantity": "575"}],
                        }
                    ]
                }
            ),
        )
    )
    (w,) = await sd.get_stock()
    assert w.products[0].SD_id == "d0_2784" and w.products[0].quantity == 575


async def test_get_balances_by_currency(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(
        return_value=httpx.Response(
            200,
            json=ok(
                {
                    "balance": [
                        {
                            "SD_id": "c1",
                            "name": "X",
                            "active": True,
                            "balance": -5.5,
                            "by-currency": [{"currency_id": "1", "amount": -5.5}],
                        }
                    ]
                }
            ),
        )
    )
    (b,) = await sd.get_balances()
    assert b.balance == -5.5 and b.by_currency[0].currency_id == "1"


async def test_get_orders_include_inside_filter(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(
        return_value=httpx.Response(
            200,
            json=ok(
                {"order": [{"SD_id": "d0_5", "code_1C": "WEB-1", "status": 1}]},
                pagination={"total": 1},
            ),
        )
    )
    orders = await sd.get_orders(date_from="2026-10-01 00:00:00", date_to="2026-10-02 00:00:00")
    assert orders[0].code_1C == "WEB-1"
    params = bodies(route)[0]["params"]
    assert "include" not in params
    assert params["filter"] == {
        "include": "all",
        "status": [1, 2, 3, 4, 5],
        "period": {"dateCreate": {"from": "2026-10-01 00:00:00", "to": "2026-10-02 00:00:00"}},
    }


# ------------------------------------------------------------------ writes


async def test_set_orders_success_and_body(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    await _seed(store)
    route.mock(
        return_value=httpx.Response(
            200,
            json={
                "status": True,
                "result": {
                    "completed": 1,
                    "error": 0,
                    "data": {
                        "order": [
                            {"CS_id": "d0_53145", "SD_id": "d0_53145", "code_1C": "WEB-TEST-0001"}
                        ]
                    },
                },
                "failed": [],
            },
        )
    )
    order = OrderIn(
        code_1C="WEB-TEST-0001",
        client=EntityID(SD_id="w3_7448"),
        agent=EntityID(SD_id="d0_3"),
        priceType=EntityID(SD_id="d0_2"),
        warehouse=EntityID(SD_id="d0_1"),
        trade=EntityID(SD_id=1),
        orderProducts=[OrderProductIn(product=EntityID(SD_id="d0_2784"), quantity=1, price=1300)],
    )
    res = await sd.set_orders([order])
    assert res.completed == 1 and res.error == 0
    assert res.ids("order")[0].SD_id == "d0_53145"
    sent = bodies(route)[0]
    assert sent["method"] == "setOrder" and "params" not in sent
    (o,) = sent["data"]["order"]
    assert o["client"] == {"SD_id": "w3_7448"}
    assert o["trade"] == {"SD_id": 1}
    assert o["status"] == 1 and o["orderProducts"][0]["discountSumma"] == 0


async def test_set_status_and_delete_bodies(
    sd: SalesDoctorClient, route: respx.Route, store: TokenStore
) -> None:
    from app.integrations.salesdoctor.schemas import OrderStatusIn

    await _seed(store)
    route.mock(return_value=httpx.Response(200, json=ok({"completed": 1, "error": 0, "data": {}})))
    await sd.set_order_status([OrderStatusIn(code_1C="WEB-1", status=3, dateShipment="2026-10-14")])
    await sd.delete_orders([{"code_1C": "WEB-1"}])
    b0, b1 = bodies(route)
    assert b0["data"]["order"] == [
        {"CS_id": "", "SD_id": "", "code_1C": "WEB-1", "status": 3, "dateShipment": "2026-10-14"}
    ]
    assert b1["data"] == {"deletedOrder": [{"CS_id": "", "SD_id": "", "code_1C": "WEB-1"}]}


async def test_filial_id_sent_when_configured(route: respx.Route, store: TokenStore) -> None:
    await _seed(store)
    route.mock(return_value=httpx.Response(200, json=ok({"warehouse": []})))
    async with httpx.AsyncClient() as http:
        c = SalesDoctorClient(
            http, store, base_url=BASE_URL, login="u", password=SecretStr("p"), filial_id=7
        )
        await c.get_warehouses()
    assert bodies(route)[0]["filial"] == {"filial_id": 7}

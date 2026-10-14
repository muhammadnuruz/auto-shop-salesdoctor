"""Frontend API gaps: shop /me balances + last price type, refs RBAC, admin order preview,
product list context, uniform 202 on admin status/delete, typed OpenAPI responses."""

from decimal import Decimal
from typing import Any, get_args

import pytest
from sqlalchemy import func, select, update

from app.api.deps import AdminContext, require_any
from app.api.schemas import OrderState
from app.core import clock
from app.models import (
    AuditLog,
    SdClientBalance,
    SdPaymentType,
    SdPrice,
    SdStock,
    ShopOrder,
    ShopOrderEvent,
    ShopOrderLine,
)
from app.models.orders import ORDER_STATES
from app.services.jobs import RECONCILE_ORDER
from tests.api.conftest import ApiFactory, make_settings
from tests.orders.conftest import World

A = "/api/admin/orders"
P = "/api/admin/products"


async def sent(w: World, lines: Any = None, price_type: str = "d0_2") -> dict[str, Any]:
    order = await w.new_order(price_type=price_type)
    o = await w.set_lines(order, lines or [("pb", 0, 2)])
    assert (await w.submit(o)).status_code == 200
    got: dict[str, Any] = (await w.shop.get(f"/api/shop/orders/{o['public_id']}")).json()
    return got


# ------------------------------------------------------------------ 1. /me balance + price type


async def test_me_balance_by_type_and_last_price_type(world: World) -> None:
    me = (await world.shop.get("/api/shop/me")).json()
    assert me["balance_by_type"] == [] and me["last_price_type_sd_id"] is None
    assert me["client"]["balance"] == "0"  # legacy field untouched

    now = clock.utcnow()
    world.session.add_all(
        [
            SdPaymentType(sd_id="d0_2", name="Cash", active=True, raw={}, synced_at=now),
            SdPaymentType(sd_id="d0_5", name="Bank", active=True, raw={}, synced_at=now),
        ]
    )
    world.session.add(
        SdClientBalance(
            client_id=world.client.id,
            balance=Decimal("-480.5"),
            by_currency=[
                {"currency_id": "d0_2", "amount": -500.5},
                {"currency_id": "d0_5", "amount": 0},  # zero: dropped
                {"currency_id": "d0_77", "amount": 20},  # not in the mirror: name null
                {"currency_id": "d0_5", "amount": None},  # unusable: dropped
                {"amount": 3},  # no id: dropped
            ],
            synced_at=now,
        )
    )
    await world.session.commit()
    me = (await world.shop.get("/api/shop/me")).json()
    assert me["balance_by_type"] == [
        {"payment_type_sd_id": "d0_2", "name": "Cash", "amount": -500.5},
        {"payment_type_sd_id": "d0_77", "name": None, "amount": 20},
    ]
    assert me["client"]["balance"] == "-480.50"

    await world.draft()  # a draft is not a submitted order
    assert (await world.shop.get("/api/shop/me")).json()["last_price_type_sd_id"] is None
    await sent(world, price_type="d0_4")
    assert (await world.shop.get("/api/shop/me")).json()["last_price_type_sd_id"] == "d0_4"
    await sent(world, [("pb", 0, 1)], price_type="d0_2")  # the most recent one wins
    assert (await world.shop.get("/api/shop/me")).json()["last_price_type_sd_id"] == "d0_2"


# ------------------------------------------------------------------------- 2. refs RBAC


async def test_require_any_helper() -> None:
    def ctx(*perms: str) -> AdminContext:
        return AdminContext(None, None, "x", frozenset(perms))  # type: ignore[arg-type]

    from fastapi import HTTPException

    dep = require_any("links.read", "orders.read", "orders.write")
    for perm in ("links.read", "orders.read", "orders.write"):
        assert (await dep(ctx(perm, "unrelated"))).permissions >= {perm}
    with pytest.raises(HTTPException) as exc:
        await dep(ctx("products.read", "orders.cancel"))
    assert exc.value.status_code == 403
    with pytest.raises(ValueError):
        require_any()


async def test_refs_open_to_order_roles(world: World, app_factory: ApiFactory) -> None:
    for role in ("operator", "viewer", "admin"):
        await world.seed.admin(f"r-{role}", role=role)
        api = await app_factory(make_settings(), sd=world.sd, jobs=world.jobs)
        assert (await api.admin_login(f"r-{role}")).status_code == 200
        assert (await api.get("/api/admin/refs")).status_code == 200, role


# --------------------------------------------------------------------------- 3. preview


async def counts(w: World) -> tuple[int, int, int]:
    await w.session.commit()
    return (
        await w.session.scalar(select(func.count()).select_from(ShopOrderEvent)) or 0,
        await w.session.scalar(select(func.count()).select_from(AuditLog)) or 0,
        await w.session.scalar(select(func.count()).select_from(ShopOrderLine)) or 0,
    )


async def test_preview_recalculates_without_persisting(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    before = await counts(world)
    calls = len(world.sd_server.calls)
    r = await world.admin.post(
        f"{A}/{o['public_id']}/preview",
        {
            "version": o["version"],
            "comment": "draft edit",
            "lines": [
                {"product_ref": "pb", "pieces": "3"},
                {"product_ref": "pa", "blocks": 1, "pieces": "1"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["version"] == o["version"] and d["comment"] == "draft edit"
    by_ref = {line["product_ref"]: line for line in d["lines"]}
    assert by_ref["pb"]["quantity"] == 3 and by_ref["pb"]["price"] == 500
    assert by_ref["pa"]["quantity"] == 13 and by_ref["pa"]["line_sum"] == 13000
    assert by_ref["pa"]["stock"] == 100 and by_ref["pa"]["warnings"] == []
    assert d["totals"] == {"qty": 16, "sum": 14500, "volume": 7.25, "lines_count": 2}
    cats = {c["category"]: c for c in d["categories"]}
    assert (
        cats["c1"]["volume"] == 6.5 and cats["c2"]["volume"] == 0.75 and cats["c2"]["sum"] == 1500
    )
    assert d["repriced"] == [] and d["price_drift"] == []
    assert d["problems"] == [] and d["events"] and d["client"]["sd_id"] == "w3_7448"

    # nothing saved, nothing sent
    assert len(world.sd_server.calls) == calls
    assert await counts(world) == before
    stored = (await world.admin.get(f"{A}/{o['public_id']}")).json()
    assert stored["version"] == o["version"] and stored["comment"] == ""
    assert [x["quantity"] for x in stored["lines"]] == [2] and stored["totals"]["sum"] == 1000
    row = await world.session.scalar(select(ShopOrder).where(ShopOrder.code_1c == o["code"]))
    assert row is not None and row.total_sum == 1000 and row.version == o["version"]


async def test_preview_matches_real_patch(world: World) -> None:
    o = await world.draft([("pb", 0, 2)])
    body = {
        "version": o["version"],
        "price_type": "d0_4",
        "lines": [{"product_ref": "pb", "pieces": "4"}, {"product_ref": "pa", "pieces": "2"}],
    }
    pre = (await world.admin.post(f"{A}/{o['public_id']}/preview", body)).json()
    real = (await world.admin.patch(f"{A}/{o['public_id']}", body)).json()
    assert real["version"] == pre["version"] + 1
    for key in ("lines", "totals", "categories", "problems", "repriced", "price_drift", "limits"):
        assert pre[key] == real[key], key
    assert pre["price_type"] == real["price_type"] == {"id": "d0_4", "name": "А-ЦЕНА"}


async def test_preview_stock_warnings_credit_own_reservation(world: World) -> None:
    o = await sent(world, [("pb", 0, 5)])  # takes all 5
    # the warehouse-refresh job (not run in tests) would have mirrored SD's remaining 0
    await world.session.execute(
        update(SdStock).where(SdStock.product_id == world.catalog["pb"].id).values(quantity=0)
    )
    await world.session.commit()
    url = f"{A}/{o['public_id']}/preview"
    same = (await world.admin.post(url, {"version": o["version"], "comment": "x"})).json()
    assert same["lines"][0]["warnings"] == [] and same["problems"] == []
    over = (
        await world.admin.post(
            url, {"version": o["version"], "lines": [{"product_ref": "pb", "pieces": "6"}]}
        )
    ).json()
    assert over["lines"][0]["warnings"] == ["over_stock"]
    assert [p["code"] for p in over["problems"]] == ["over_stock"]


async def test_preview_reprice_and_price_drift(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    await world.session.execute(
        update(SdPrice).where(SdPrice.price == 500).values(price=Decimal(600))  # pb in d0_2 moves
    )
    await world.session.commit()
    url = f"{A}/{o['public_id']}/preview"
    kept = (await world.admin.post(url, {"version": o["version"]})).json()
    assert kept["lines"][0]["price"] == 500 and kept["totals"]["sum"] == 1000
    assert kept["price_drift"] == [{"product_ref": "pb", "kept_price": 500, "current_price": 600}]
    assert kept["repriced"] == []
    repriced = (await world.admin.post(url, {"version": o["version"], "reprice": True})).json()
    assert repriced["lines"][0]["price"] == 600 and repriced["totals"]["sum"] == 1200
    assert repriced["repriced"] == [{"product_ref": "pb", "old": 500, "new": 600}]
    assert repriced["price_drift"] == []
    stored = (await world.admin.get(f"{A}/{o['public_id']}")).json()
    assert stored["totals"]["sum"] == 1000 and stored["lines"][0]["price"] == 500


async def test_preview_errors_and_rbac(world: World, app_factory: ApiFactory) -> None:
    o = await sent(world)
    url = f"{A}/{o['public_id']}/preview"
    v = o["version"]
    stale = await world.admin.post(url, {"version": v + 5})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "version_conflict"
    bad_header = await world.admin.post(url, {"version": v, "agent": "nope"})
    assert bad_header.status_code == 422 and bad_header.json()["detail"]["code"] == "invalid_header"
    bad_line = await world.admin.post(
        url, {"version": v, "lines": [{"product_ref": "zzz", "pieces": "1"}]}
    )
    assert bad_line.status_code == 422 and bad_line.json()["detail"]["code"] == "invalid_lines"
    bad_date = await world.admin.post(url, {"version": v, "shipment_date": "2001-01-01"})
    assert bad_date.status_code == 422
    assert (await world.admin.post(url, {"version": v}, csrf=False)).status_code == 403
    assert (await world.admin.post(f"{A}/{o['public_id']}/preview", {})).status_code == 422
    unknown = await world.admin.post(
        f"{A}/00000000-0000-0000-0000-000000000000/preview", {"version": 1}
    )
    assert unknown.status_code == 404

    await world.seed.admin("view", role="viewer")
    viewer = await app_factory(make_settings(), sd=world.sd, jobs=world.jobs)
    assert (await viewer.admin_login("view")).status_code == 200
    assert (await viewer.post(url, {"version": v})).status_code == 403
    anon = await app_factory(make_settings())
    assert (await anon.post(url, {"version": v})).status_code == 401

    await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 5})  # cancelled
    gone = await world.admin.post(url, {"version": v + 1})
    assert gone.status_code == 409 and gone.json()["detail"]["code"] == "order_not_editable"


async def test_preview_works_for_draft_and_keeps_db_clean(world: World) -> None:
    o = await world.draft([("pb", 0, 1)])
    before = await counts(world)
    r = await world.admin.post(
        f"{A}/{o['public_id']}/preview",
        {"version": o["version"], "warehouse": "d0_1", "lines": []},
    )
    assert r.status_code == 200 and r.json()["totals"]["lines_count"] == 0
    assert "no_lines" in [p["code"] for p in r.json()["problems"]]
    assert await counts(world) == before
    assert (await world.admin.get(f"{A}/{o['public_id']}")).json()["totals"]["lines_count"] == 1


# --------------------------------------------------------------------- 4. product context


async def test_product_list_price_and_stock_context(world: World) -> None:
    async def items(**params: Any) -> dict[str, dict[str, Any]]:
        r = await world.admin.get(P, params={"limit": 200, **params})
        assert r.status_code == 200, r.text
        return {i["sd_id"]: i for i in r.json()["items"]}

    plain = await items()
    assert all("price" not in i and "stock_qty" not in i for i in plain.values())
    both = await items(price_type="d0_4", warehouse="d0_1")
    assert both["pa"]["price"] == 900 and both["pa"]["stock_qty"] == 100
    assert both["pnoprice"]["price"] is None  # no price in that type
    assert both["p1"]["price"] is None and both["p1"]["stock_qty"] == 0  # no stock row
    assert both["pb"]["price"] == 450 and both["pb"]["stock_qty"] == 5
    # existing lists are unchanged
    assert (
        both["pa"]["stock"] == plain["pa"]["stock"]
        and both["pa"]["prices"] == plain["pa"]["prices"]
    )
    only_price = await items(price_type="d0_2")
    assert only_price["pa"]["price"] == 1000 and "stock_qty" not in only_price["pa"]
    only_wh = await items(warehouse="d0_1")
    assert only_wh["pa"]["stock_qty"] == 100 and "price" not in only_wh["pa"]
    for bad in ({"price_type": "d0_7"}, {"price_type": "nope"}, {"warehouse": "nope"}):
        r = await world.admin.get(P, params=bad)
        assert r.status_code == 422, bad


# ------------------------------------------------------------------- 5. uniform 202 bodies


async def test_status_unknown_returns_full_order_body(world: World) -> None:
    o = await sent(world)
    world.sd_server.status_script = ["timeout"]
    r = await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 2})
    assert r.status_code == 202, r.text
    d = r.json()
    assert d["state"] == "outcome_unknown" and d["public_id"] == o["public_id"]
    assert d["lines"] and d["totals"]["sum"] == 1000 and d["warnings"] == []
    assert d["last_error"]["code"] == "outcome_unknown" and "detail" not in d
    assert d["events"][-1]["event"] == "outcome_unknown"
    assert RECONCILE_ORDER in world.jobs.names()


async def test_delete_unknown_returns_full_order_body(world: World) -> None:
    o = await sent(world)
    world.sd_server.delete_script = ["timeout"]
    r = await world.admin.delete(f"{A}/{o['public_id']}")
    assert r.status_code == 202, r.text
    d = r.json()
    assert d["state"] == "outcome_unknown" and d["public_id"] == o["public_id"]
    assert d["lines"] and "detail" not in d and d["client"]["id"] == world.client.id


# ---------------------------------------------------------------------------- 6. OpenAPI


def test_order_state_literal_matches_model() -> None:
    assert get_args(OrderState) == ORDER_STATES


async def test_openapi_has_typed_responses(world: World) -> None:
    spec = (await world.admin.get("/openapi.json")).json()

    def ref_of(path: str, method: str, code: str = "200") -> str:
        schema = spec["paths"][path][method]["responses"][code]["content"]["application/json"][
            "schema"
        ]
        assert "$ref" in schema or "items" in schema, (path, method, schema)
        return str(schema.get("$ref", schema.get("items", {}).get("$ref", "")))

    typed = {
        ("/api/shop/me", "get"): "MeOut",
        ("/api/shop/catalog", "get"): "CatalogOut",
        ("/api/shop/catalog/categories", "get"): "CategoriesOut",
        ("/api/shop/catalog/barcode/{code}", "get"): "BarcodeItemsOut",
        ("/api/shop/stock", "get"): "StockOut",
        ("/api/shop/orders", "get"): "ShopOrderListOut",
        ("/api/shop/orders/{public_id}", "get"): "OrderOut",
        ("/api/shop/orders/{public_id}/submit", "post"): "OrderOut",
        ("/api/shop/orders/{public_id}/lines", "put"): "OrderOut",
        ("/api/shop/orders/import", "post"): "ImportPreviewOut",
        ("/api/admin/orders", "get"): "AdminOrderListOut",
        ("/api/admin/orders/{public_id}", "get"): "AdminOrderOut",
        ("/api/admin/orders/{public_id}", "patch"): "AdminOrderOut",
        ("/api/admin/orders/{public_id}/preview", "post"): "AdminOrderOut",
        ("/api/admin/orders/{public_id}/status", "post"): "AdminOrderOut",
        ("/api/admin/orders/{public_id}", "delete"): "AdminOrderOut",
        ("/api/admin/dashboard", "get"): "DashboardOut",
        ("/api/admin/refs", "get"): "RefsOut",
        ("/api/admin/products", "get"): "ProductListOut",
        ("/api/admin/products/{sd_id}", "patch"): "ProductOut",
    }
    for (path, method), name in typed.items():
        assert ref_of(path, method).endswith("/" + name), (path, method)
    submit = spec["paths"]["/api/shop/orders/{public_id}/submit"]["post"]["responses"]
    assert "202" in submit
    props = spec["components"]["schemas"]["MeOut"]["properties"]
    assert {"balance_by_type", "last_price_type_sd_id"} <= set(props)
    assert "enum" in spec["components"]["schemas"]["OrderSummaryOut"]["properties"]["state"]

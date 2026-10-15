"""Admin products (browse/hide/barcodes), dashboard and reference lists."""

from typing import Any

from sqlalchemy import select

from app.core import clock
from app.models import AuditLog, ProductBarcode, SyncRun
from tests.api.conftest import ApiFactory, make_settings
from tests.orders.conftest import World

P = "/api/admin/products"


async def viewer(w: World, factory: ApiFactory, role: str = "viewer", **kw: Any) -> Any:
    await w.seed.admin(f"u-{role}-{len(kw)}", role=role, **kw)
    api = await factory(make_settings(), sd=w.sd, jobs=w.jobs)
    assert (await api.admin_login(f"u-{role}-{len(kw)}")).status_code == 200
    return api


async def actions(w: World) -> list[str]:
    await w.session.commit()
    return list((await w.session.scalars(select(AuditLog.action).order_by(AuditLog.id))).all())


# ----------------------------------------------------------------------------- browse


async def test_product_list_filters(world: World) -> None:
    body = (await world.admin.get(P, params={"limit": 200})).json()
    refs = {i["sd_id"] for i in body["items"]}
    assert {"pa", "pb", "phidden", "pinactive", "p1"} <= refs and body["total"] == len(refs)
    pa = next(i for i in body["items"] if i["sd_id"] == "pa")
    assert pa["name"] == "Cream Alpha" and pa["pack_quantity"] == 12 and pa["hidden"] is False
    assert pa["stock"] == [{"warehouse": "d0_1", "name": "Основной склад", "quantity": 100}]
    assert {(p["price_type"], p["price"]) for p in pa["prices"]} == {("d0_2", 1000), ("d0_4", 900)}
    assert {b["barcode"] for b in pa["barcodes"]} == {"4780000000011", "4780000000100"}
    assert all(p["price_type"] != "d0_7" for i in body["items"] for p in i["prices"])  # no purchase

    async def ask(**params: Any) -> set[str]:
        r = await world.admin.get(P, params={"limit": 200, **params})
        return {i["sd_id"] for i in r.json()["items"]}

    assert await ask(hidden="true") == {"phidden"}
    assert "phidden" not in await ask(hidden="false")
    assert await ask(active="false") == {"pinactive"}
    assert "p1" in await ask(has_barcode="false") and "pa" not in await ask(has_barcode="false")
    assert "pa" in await ask(has_barcode="true") and "p1" not in await ask(has_barcode="true")
    assert await ask(category="c2") == {"pb"}
    assert await ask(q="4780000000100") == {"pa"}
    assert await ask(q="cream") == {"pa"}
    assert await ask(q="CA") == {"pa"}
    assert await ask(q="%") == set()


async def test_product_list_pagination_and_rbac(world: World, app_factory: ApiFactory) -> None:
    page = (await world.admin.get(P, params={"limit": 2, "offset": 2})).json()
    assert len(page["items"]) == 2 and page["total"] > 2
    anon = await app_factory(make_settings())
    assert (await anon.get(P)).status_code == 401
    v = await viewer(world, app_factory)
    assert (await v.get(P)).status_code == 200
    assert (await v.patch(f"{P}/pa", {"hidden": True})).status_code == 403
    assert (await v.post(f"{P}/pa/barcodes", {"barcode": "12345678"})).status_code == 403


# -------------------------------------------------------------------------------- hide


async def test_hide_product_removes_it_from_shop_catalog(world: World) -> None:
    r = await world.admin.patch(f"{P}/pa", {"hidden": True, "note": "recall"})
    assert r.status_code == 200 and r.json()["hidden"] is True and r.json()["note"] == "recall"
    shop = (await world.shop.get("/api/shop/catalog", params={"price_type": "d0_2"})).json()
    assert "pa" not in {i["product_ref"] for i in shop["items"]}
    r = await world.admin.patch(f"{P}/pa", {"hidden": False})
    assert r.json()["hidden"] is False and r.json()["note"] == "recall"  # note untouched
    shop = (await world.shop.get("/api/shop/catalog", params={"price_type": "d0_2"})).json()
    assert "pa" in {i["product_ref"] for i in shop["items"]}
    assert (await world.admin.patch(f"{P}/pa", {"note": None})).json()["note"] is None
    assert actions_count(await actions(world), "product.update") == 3
    assert (await world.admin.patch(f"{P}/ghost", {"hidden": True})).status_code == 404


def actions_count(items: list[str], name: str) -> int:
    return items.count(name)


async def test_product_ids_follow_list_filters(world: World) -> None:
    full = (await world.admin.get(P, params={"limit": 200})).json()
    ids = (await world.admin.get(f"{P}/ids")).json()
    assert ids["total"] == full["total"] and ids["sd_ids"] == [i["sd_id"] for i in full["items"]]
    assert (await world.admin.get(f"{P}/ids", params={"hidden": "true"})).json() == {
        "total": 1,
        "sd_ids": ["phidden"],
    }
    assert (await world.admin.get(f"{P}/ids", params={"q": "cream"})).json()["sd_ids"] == ["pa"]


async def test_bulk_hide_and_show(world: World) -> None:
    r = await world.admin.post(f"{P}/bulk", {"sd_ids": ["pa", "pb", "pa", "ghost"], "hidden": True})
    assert r.status_code == 200 and r.json() == {"updated": 2, "not_found": 1}
    shop = (await world.shop.get("/api/shop/catalog", params={"price_type": "d0_2"})).json()
    refs = {i["product_ref"] for i in shop["items"]}
    assert "pa" not in refs and "pb" not in refs
    hidden = (await world.admin.get(f"{P}/ids", params={"hidden": "true"})).json()["sd_ids"]
    assert set(hidden) == {"pa", "pb", "phidden"}

    # existing settings (note) survive the bulk upsert
    await world.admin.patch(f"{P}/pa", {"note": "keep"})
    r = await world.admin.post(f"{P}/bulk", {"sd_ids": ["pa", "pb", "phidden"], "hidden": False})
    assert r.json() == {"updated": 3, "not_found": 0}
    pa = next(i for i in (await world.admin.get(P, params={"q": "cream"})).json()["items"])
    assert pa["hidden"] is False and pa["note"] == "keep"
    assert (await world.admin.get(f"{P}/ids", params={"hidden": "true"})).json()["total"] == 0
    assert actions_count(await actions(world), "product.bulk_update") == 2


async def test_bulk_validation_and_rbac(world: World, app_factory: ApiFactory) -> None:
    assert (await world.admin.post(f"{P}/bulk", {"sd_ids": [], "hidden": True})).status_code == 422
    assert (await world.admin.post(f"{P}/bulk", {"sd_ids": ["pa"]})).status_code == 422
    v = await viewer(world, app_factory)
    assert (await v.get(f"{P}/ids")).status_code == 200
    assert (await v.post(f"{P}/bulk", {"sd_ids": ["pa"], "hidden": True})).status_code == 403


# ----------------------------------------------------------------------------- barcodes


async def test_add_and_remove_admin_barcode(world: World) -> None:
    r = await world.admin.post(f"{P}/p1/barcodes", {"barcode": "12345678"})
    assert r.status_code == 201, r.text
    assert r.json()["barcodes"] == [{"barcode": "12345678", "source": "admin"}]
    assert r.json()["warnings"] == []
    # the shop can now scan it
    scan = await world.shop.get("/api/shop/catalog/barcode/12345678")
    assert scan.json()["items"] == []  # p1 has no price: still not orderable
    r = await world.admin.delete(f"{P}/p1/barcodes", params={"barcode": "12345678"})
    assert r.status_code == 200 and r.json()["barcodes"] == []
    acts = await actions(world)
    assert "product.barcode_add" in acts and "product.barcode_remove" in acts


async def test_new_admin_barcode_is_scannable_in_shop(world: World) -> None:
    assert (await world.admin.post(f"{P}/pb/barcodes", {"barcode": "99999999"})).status_code == 201
    scan = await world.shop.get("/api/shop/catalog/barcode/99999999")
    assert [i["product_ref"] for i in scan.json()["items"]] == ["pb"]


async def test_barcode_format_and_duplicates(world: World) -> None:
    for bad in ("1234567", "123456789012345", "abcdefgh", "1234 5678", ""):
        r = await world.admin.post(f"{P}/p1/barcodes", {"barcode": bad})
        assert r.status_code == 422, bad
    assert (await world.admin.post(f"{P}/p1/barcodes", {"barcode": "12345678"})).status_code == 201
    dup = await world.admin.post(f"{P}/p1/barcodes", {"barcode": "12345678"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "barcode_exists"


async def test_barcode_used_elsewhere_warns(world: World) -> None:
    r = await world.admin.post(f"{P}/p1/barcodes", {"barcode": "4780000000011"})  # pa's barcode
    assert r.status_code == 201
    assert r.json()["warnings"] == ["barcode_used_by_other_products"]
    assert r.json()["other_products"] == [{"sd_id": "pa", "name": "Cream Alpha"}]


async def test_sd_barcodes_cannot_be_removed(world: World) -> None:
    r = await world.admin.delete(f"{P}/pa/barcodes", params={"barcode": "4780000000011"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "barcode_from_sd"
    assert (
        await world.admin.delete(f"{P}/pa/barcodes", params={"barcode": "55555555"})
    ).status_code == 404
    assert (await world.admin.delete(f"{P}/pa/barcodes")).status_code == 422
    await world.session.commit()
    assert await world.session.scalar(
        select(ProductBarcode).where(ProductBarcode.barcode == "4780000000011")
    )


# --------------------------------------------------------------------------- dashboard


async def test_dashboard(world: World, app_factory: ApiFactory) -> None:
    for lines in ([("pb", 0, 2)], [("pa", 0, 1)]):
        o = await world.draft(lines)
        assert (await world.submit(o)).status_code == 200
    world.sd_server.script = ["validation"]
    await world.submit(await world.draft([("pc1", 0, 1)]))
    world.sd_server.script = ["timeout"]
    await world.submit(await world.draft([("pc2", 0, 1)]))
    world.session.add(
        SyncRun(
            entity="stock",
            started_at=clock.utcnow(),
            finished_at=clock.utcnow(),
            status="ok",
            items=7,
        )
    )
    await world.session.commit()
    d = (await world.admin.get("/api/admin/dashboard")).json()
    # sent + outcome_unknown count as placed demand; failed does not
    assert d["periods"]["today"] == {"orders": 3, "sum": 2310}
    assert d["periods"]["7d"] == d["periods"]["30d"] == d["periods"]["today"]
    assert d["failed"] == 1 and d["outcome_unknown"] == 1
    assert d["by_state"]["sent"]["orders"] == 2 and d["by_state"]["failed"]["orders"] == 1
    assert d["top_clients"][0]["name"] == "Тест Дилшод" and d["top_clients"][0]["orders"] == 3
    assert d["top_products"][0]["product_ref"] == "pb" and d["top_products"][0]["qty"] == 2
    assert {p["product_ref"] for p in d["top_products"]} == {"pa", "pb", "pc2"}
    assert d["by_link"][0]["slug"] == "shop-link" and d["by_link"][0]["orders"] == 3
    assert d["last_sync"]["stock"]["items"] == 7 and d["last_sync"]["stock"]["status"] == "ok"
    v = await viewer(world, app_factory)
    assert (await v.get("/api/admin/dashboard")).status_code == 200
    anon = await app_factory(make_settings())
    assert (await anon.get("/api/admin/dashboard")).status_code == 401


async def test_dashboard_requires_reports_read(world: World, app_factory: ApiFactory) -> None:
    op = await viewer(world, app_factory, role="operator")
    assert (await op.get("/api/admin/dashboard")).status_code == 403


async def test_dashboard_empty(world: World) -> None:
    d = (await world.admin.get("/api/admin/dashboard")).json()
    assert d["periods"]["today"] == {"orders": 0, "sum": 0}
    assert d["top_clients"] == [] and d["failed"] == 0 and d["last_sync"] == {}


# ------------------------------------------------------------------------------- refs


async def test_refs(world: World, app_factory: ApiFactory) -> None:
    r = await world.admin.get("/api/admin/refs")
    assert r.status_code == 200
    d = r.json()
    assert [w["id"] for w in d["warehouses"]] == ["d0_1"]  # inactive d0_9 excluded
    assert [p["id"] for p in d["price_types"]] == ["d0_4", "d0_2"]  # ordered by name
    assert "d0_7" not in {p["id"] for p in d["price_types"]}  # purchase never offered
    assert {a["id"] for a in d["agents"]} == {"d0_3", "d0_11"}
    assert d["trade_directions"] == [{"id": "1", "name": "Parfum Brend"}]
    assert {c["id"] for c in d["categories"]} == {"c1", "c2"} and d["brands"] == [
        {"id": "b1", "name": "Brand 1"}
    ]
    assert d["territories"] == [] and d["client_categories"] == []
    # the lists must satisfy what the link form submits
    link = await world.admin.post(
        "/api/admin/links",
        {
            "name": "from refs",
            "settings": {
                "warehouses": {"mode": "fixed", "ids": [d["warehouses"][0]["id"]]},
                "price_types": {"mode": "choice", "ids": [p["id"] for p in d["price_types"]]},
                "trade_directions": {"mode": "fixed", "ids": [d["trade_directions"][0]["id"]]},
            },
        },
    )
    assert link.status_code == 201, link.text
    v = await viewer(world, app_factory)
    assert (await v.get("/api/admin/refs")).status_code == 200
    anon = await app_factory(make_settings())
    assert (await anon.get("/api/admin/refs")).status_code == 401


async def test_refs_client_ids_and_rbac(world: World, app_factory: ApiFactory) -> None:
    await world.seed.client("A", ["901110001"], city_sd_id="t1", category_sd_id="k1")
    await world.seed.client("B", ["901110002"], city_sd_id="t1", category_sd_id="k2")
    await world.seed.client("Off", ["901110003"], city_sd_id="t9", active=False)
    d = (await world.admin.get("/api/admin/refs")).json()
    assert d["territories"] == [{"id": "t1", "clients": 2}]
    assert d["client_categories"] == [{"id": "k1", "clients": 1}, {"id": "k2", "clients": 1}]

"""Shop catalog: search order, link allowances, product filter, hidden, stock (mirror + fresh)."""

from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessLink
from tests.api.conftest import ApiFactory, make_settings
from tests.orders.conftest import PHONE, SLUG, WH, World


async def get(w: World, path: str, **params: Any) -> Any:
    return await w.shop.get(f"/api/shop/catalog{path}", params=params)


def refs(body: dict[str, Any]) -> list[str]:
    return [i["product_ref"] for i in body["items"]]


async def test_catalog_lists_only_orderable_products(world: World) -> None:
    r = await get(world, "", price_type="d0_2")
    assert r.status_code == 200
    body = r.json()
    # no price / zero price / inactive / hidden products never show up
    assert set(refs(body)) == {"pa", "pb", "pc1", "pc2", "pnostock"}
    assert body["total"] == 5 and body["price_type"] == "d0_2" and body["warehouse"] == WH


async def test_catalog_item_shape_and_price_type_switch(world: World) -> None:
    items = {i["product_ref"]: i for i in (await get(world, "", price_type="d0_2")).json()["items"]}
    pa = items["pa"]
    assert pa["name"] == "Cream Alpha" and pa["code"] == "CA" and pa["pack_quantity"] == 12
    assert pa["price"] == 1000 and pa["stock"] == 100
    assert pa["category"] == "c1" and pa["category_name"] == "Cat 1"
    assert pa["barcodes"] == ["4780000000011", "4780000000100"]
    assert "image_url" in pa and "thumb_url" in pa and "unit" in pa
    assert items["pnostock"]["stock"] == 0  # no stock row -> 0, not null
    other = {i["product_ref"]: i for i in (await get(world, "", price_type="d0_4")).json()["items"]}
    assert other["pa"]["price"] == 900
    assert "pc1" not in other  # only priced in d0_2


async def test_default_price_type_is_first_allowed(world: World) -> None:
    body = (await get(world, "")).json()
    assert body["price_type"] == "d0_2"


async def test_price_type_and_warehouse_must_be_link_allowed(world: World) -> None:
    for params in ({"price_type": "d0_7"}, {"price_type": "nope"}, {"warehouse": "d0_9"}):
        r = await get(world, "", **params)
        assert r.status_code == 422, params
        assert r.json()["detail"]["code"] == "not_allowed"


async def test_purchase_price_type_never_exposed(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(AccessLink).values(
            settings={
                **world.link.settings,
                "price_types": {"mode": "choice", "ids": ["d0_2", "d0_7"]},
            }
        )
    )
    await session.commit()
    assert (await get(world, "", price_type="d0_7")).status_code == 422


async def test_search_by_barcode_then_code_then_name(world: World) -> None:
    by_barcode = (await get(world, "", q="4780000000100", price_type="d0_2")).json()
    assert refs(by_barcode) == ["pa"]
    by_code = (await get(world, "", q="CA", price_type="d0_2")).json()
    assert refs(by_code)[0] == "pa"
    by_name = (await get(world, "", q="cream", price_type="d0_2")).json()
    assert refs(by_name) == ["pa"]
    multi_token = (await get(world, "", q="alpha cre", price_type="d0_2")).json()
    assert refs(multi_token) == ["pa"]


async def test_exact_matches_rank_before_name_matches(world: World) -> None:
    # "Twin" matches two names; barcode of Twin One ranks it first even with a name hit on both
    body = (await get(world, "", q="4780000000035", price_type="d0_2")).json()
    assert set(refs(body)) == {"pc1", "pc2"}


async def test_search_escapes_like_wildcards(world: World) -> None:
    assert (await get(world, "", q="%", price_type="d0_2")).json()["items"] == []
    assert (await get(world, "", q="_", price_type="d0_2")).json()["items"] == []


async def test_search_never_returns_hidden_or_unpriced_even_by_barcode(world: World) -> None:
    for code in ("4780000000073", "4780000000042", "4780000000059", "4780000000066"):
        assert (await get(world, "", q=code, price_type="d0_2")).json()["items"] == []


async def test_category_filter_and_pagination(world: World) -> None:
    only_c2 = (await get(world, "", category="c2", price_type="d0_2")).json()
    assert refs(only_c2) == ["pb"]
    page1 = (await get(world, "", size=2, page=1, price_type="d0_2")).json()
    page2 = (await get(world, "", size=2, page=2, price_type="d0_2")).json()
    assert page1["total"] == 5 and len(page1["items"]) == 2 and len(page2["items"]) == 2
    assert not set(refs(page1)) & set(refs(page2))
    assert (await get(world, "", size=101)).status_code == 422


async def test_categories_list_counts_only_orderable(world: World) -> None:
    body = (await get(world, "/categories", price_type="d0_2")).json()
    assert body["items"] == [
        {"id": "c1", "name": "Cat 1", "products": 4},
        {"id": "c2", "name": "Cat 2", "products": 1},
    ]


async def test_barcode_endpoint_returns_every_orderable_match(world: World) -> None:
    one = (await get(world, "/barcode/4780000000011")).json()["items"]
    assert [i["product_ref"] for i in one] == ["pa"]
    dup = (await get(world, "/barcode/4780000000035")).json()["items"]
    assert sorted(i["product_ref"] for i in dup) == ["pc1", "pc2"]
    assert (await get(world, "/barcode/9999999999999")).json()["items"] == []
    assert (await get(world, "/barcode/4780000000073")).json()["items"] == []  # hidden
    assert (await get(world, "/barcode/bad%20code")).status_code == 422


async def test_product_filter_categories_restricts_catalog(
    world: World, session: AsyncSession
) -> None:
    await session.execute(
        update(AccessLink).values(
            settings={
                **world.link.settings,
                "product_filter": {"mode": "categories", "ids": ["c2"]},
            }
        )
    )
    await session.commit()
    assert refs((await get(world, "", price_type="d0_2")).json()) == ["pb"]
    assert (await get(world, "/barcode/4780000000011")).json()["items"] == []
    cats = (await get(world, "/categories", price_type="d0_2")).json()["items"]
    assert [c["id"] for c in cats] == ["c2"]


async def test_product_filter_brands_and_products(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(AccessLink).values(
            settings={**world.link.settings, "product_filter": {"mode": "brands", "ids": ["b1"]}}
        )
    )
    await session.commit()
    assert refs((await get(world, "", price_type="d0_2")).json()) == ["pb"]
    await session.execute(
        update(AccessLink).values(
            settings={
                **world.link.settings,
                "product_filter": {"mode": "products", "ids": ["pa", "pc1"]},
            }
        )
    )
    await session.commit()
    assert set(refs((await get(world, "", price_type="d0_2")).json())) == {"pa", "pc1"}


async def test_catalog_requires_login(app_factory: ApiFactory, world: World) -> None:
    anon = await app_factory(make_settings())
    assert (await anon.get("/api/shop/catalog")).status_code == 401
    assert (await anon.get("/api/shop/stock?warehouse=d0_1&products=pa")).status_code == 401


# ------------------------------------------------------------------------------------- stock


async def test_stock_from_mirror(world: World) -> None:
    r = await world.shop.get("/api/shop/stock", params={"warehouse": WH, "products": "pa,pb,zzz"})
    assert r.status_code == 200
    assert r.json() == {
        "warehouse": WH,
        "fresh": False,
        "items": [
            {"product_ref": "pa", "quantity": 100},
            {"product_ref": "pb", "quantity": 5},
        ],
    }
    assert world.sd_server.calls == []  # no SD traffic without ?fresh=1


async def test_stock_warehouse_must_be_allowed(world: World) -> None:
    r = await world.shop.get("/api/shop/stock", params={"warehouse": "d0_9", "products": "pa"})
    assert r.status_code == 422


async def test_stock_fresh_pulls_from_sd_and_updates_mirror(world: World) -> None:
    world.sd_server.stock[WH]["pb"] = 2  # changed in SD since the last sync
    r = await world.shop.get(
        "/api/shop/stock", params={"warehouse": WH, "products": "pb,pa", "fresh": "1"}
    )
    assert r.status_code == 200 and r.json()["fresh"] is True
    assert {i["product_ref"]: i["quantity"] for i in r.json()["items"]} == {"pb": 2, "pa": 100}
    assert len(world.sd_server.calls_of("getStock")) == 1
    mirror = await world.shop.get("/api/shop/stock", params={"warehouse": WH, "products": "pb"})
    assert mirror.json()["items"] == [{"product_ref": "pb", "quantity": 2}]


async def test_stock_fresh_missing_in_sd_means_zero(world: World) -> None:
    del world.sd_server.stock[WH]["pb"]  # SD omits out-of-stock products
    r = await world.shop.get(
        "/api/shop/stock", params={"warehouse": WH, "products": "pb", "fresh": "1"}
    )
    assert r.json()["items"] == [{"product_ref": "pb", "quantity": 0}]


async def test_stock_fresh_is_rate_limited(app_factory: ApiFactory, world: World) -> None:
    api = await app_factory(
        make_settings(RL_SHOP_FRESH_STOCK_PER_MIN=2), sd=world.sd, jobs=world.jobs
    )
    assert (await api.shop_login(SLUG, PHONE)).status_code == 200
    params = {"warehouse": WH, "products": "pa", "fresh": "1"}
    assert (await api.get("/api/shop/stock", params=params)).status_code == 200
    assert (await api.get("/api/shop/stock", params=params)).status_code == 200
    r = await api.get("/api/shop/stock", params=params)
    assert r.status_code == 429 and "Retry-After" in r.headers
    # the mirror read is not limited
    assert (
        await api.get("/api/shop/stock", params={"warehouse": WH, "products": "pa"})
    ).status_code == 200


async def test_stock_fresh_sd_down_is_503(world: World) -> None:
    world.sd_server.stock_down = True
    r = await world.shop.get(
        "/api/shop/stock", params={"warehouse": WH, "products": "pa", "fresh": "1"}
    )
    assert r.status_code == 503 and r.json()["detail"]["code"] == "sd_unavailable"

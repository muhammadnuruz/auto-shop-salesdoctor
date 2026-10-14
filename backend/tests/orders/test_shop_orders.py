"""Shop draft editing: quantity math, server-side prices, allowances, filters, versions, scope."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessLink, ProductSetting, SdClient, ShopOrder, ShopOrderEvent
from tests.api.conftest import ApiFactory, make_settings
from tests.orders.conftest import KEY, SLUG, World

URL = "/api/shop/orders"


def line(order: dict[str, Any], ref: str) -> dict[str, Any]:
    return next(ln for ln in order["lines"] if ln["product_ref"] == ref)


async def set_link(w: World, session: AsyncSession, **over: Any) -> None:
    await session.execute(update(AccessLink).values(settings={**w.link.settings, **over}))
    await session.commit()


# -------------------------------------------------------------------------------- create


async def test_create_draft_autofills_single_options(world: World) -> None:
    r = await world.shop.post(URL, {})
    assert r.status_code == 201
    o = r.json()
    assert o["state"] == "draft" and o["version"] == 1 and o["editable"] is True
    assert o["code"].startswith("WEB-") and len(o["code"]) == len("WEB-000001")
    assert o["warehouse"]["id"] == "d0_1" and o["trade"]["id"] == "1"
    assert o["agent"]["id"] == "d0_11"  # the client's assigned agent
    assert o["price_type"] is None  # two options -> the shop must choose
    assert o["lines"] == [] and o["totals"]["sum"] == 0
    assert "price_type" in {p.get("field") for p in o["problems"]}


async def test_code_1c_is_zero_padded_id_and_unique(world: World, session: AsyncSession) -> None:
    a = (await world.shop.post(URL, {})).json()
    b = (await world.shop.post(URL, {})).json()
    assert a["code"] != b["code"]
    rows = (await session.scalars(select(ShopOrder).order_by(ShopOrder.id))).all()
    assert [r.code_1c for r in rows] == [f"WEB-{r.id:06d}" for r in rows]
    assert rows[0].client_id == world.client.id and rows[0].link_id == world.link.id


async def test_create_event_written(world: World, session: AsyncSession) -> None:
    await world.shop.post(URL, {})
    ev = (await session.scalars(select(ShopOrderEvent))).all()
    assert [e.event for e in ev] == ["created"] and ev[0].actor_type == "shop"


async def test_header_must_be_link_allowed(world: World) -> None:
    for body in (
        {"price_type": "d0_7"},  # purchase
        {"warehouse": "d0_9"},  # not in link
        {"agent": "d0_3"},  # not assigned to this client
        {"trade": "999"},
    ):
        r = await world.shop.post(URL, body)
        assert r.status_code == 422, body
        assert r.json()["detail"]["code"] == "invalid_header"


async def test_shipment_date_validation(world: World) -> None:
    assert (await world.shop.post(URL, {"shipment_date": "2000-01-01"})).status_code == 422
    assert (await world.shop.post(URL, {"shipment_date": "2999-01-01"})).status_code == 422


async def test_draft_limit(world: World) -> None:
    for _ in range(30):
        assert (await world.shop.post(URL, {})).status_code == 201
    r = await world.shop.post(URL, {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "too_many_drafts"


# ------------------------------------------------------------------- allowance modes


async def test_agent_choice_mode_and_client_assigned(world: World, session: AsyncSession) -> None:
    await set_link(world, session, agents={"mode": "choice", "ids": ["d0_3", "d0_11"]})
    o = (await world.shop.post(URL, {})).json()
    assert o["agent"] is None  # two options, nothing picked yet
    r = await world.shop.patch(f"{URL}/{o['public_id']}", {"version": 1, "agent": "d0_3"})
    assert r.status_code == 200 and r.json()["agent"]["id"] == "d0_3"


async def test_agent_fixed_mode(world: World, session: AsyncSession) -> None:
    await set_link(world, session, agents={"mode": "fixed", "ids": ["d0_3"]})
    o = (await world.shop.post(URL, {})).json()
    assert o["agent"]["id"] == "d0_3"
    r = await world.shop.patch(f"{URL}/{o['public_id']}", {"version": 1, "agent": "d0_11"})
    assert r.status_code == 422


async def test_client_assigned_ignores_inactive_agent(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(SdClient)
        .where(SdClient.id == world.client.id)
        .values(agents=[{"id": "d0_11"}, {"id": "d0_3"}])
    )
    from app.models import SdAgent

    await session.execute(update(SdAgent).where(SdAgent.sd_id == "d0_3").values(active=False))
    await session.commit()
    assert (await world.shop.post(URL, {})).json()["agent"]["id"] == "d0_11"


async def test_warehouse_choice_mode(world: World, session: AsyncSession) -> None:
    await set_link(world, session, warehouses={"mode": "choice", "ids": ["d0_1"]})
    assert (await world.shop.post(URL, {})).json()["warehouse"]["id"] == "d0_1"


# ---------------------------------------------------------------------- quantity math


async def test_quantity_is_blocks_times_pack_plus_pieces(world: World) -> None:
    o = await world.new_order()
    o = await world.set_lines(o, [("pa", 2, 3)])  # 2*12 + 3
    ln = line(o, "pa")
    assert ln["quantity"] == 27 and ln["blocks"] == 2 and ln["pieces"] == 3
    assert ln["price"] == 1000 and ln["line_sum"] == 27000
    assert o["totals"] == {"qty": 27, "sum": 27000, "volume": 13.5, "lines_count": 1}


async def test_blocks_disallowed_without_pack(world: World) -> None:
    o = await world.new_order()
    r = await world.shop.put(
        f"{URL}/{o['public_id']}/lines",
        {"version": o["version"], "lines": [{"product_ref": "pb", "blocks": 1, "pieces": "0"}]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["code"] == "blocks_not_allowed"


async def test_zero_and_fractional_quantity_rejected(world: World) -> None:
    o = await world.new_order()
    r = await world.shop.put(
        f"{URL}/{o['public_id']}/lines",
        {"version": 1 + 1, "lines": [{"product_ref": "pb", "blocks": 0, "pieces": "0"}]},
    )
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "zero_quantity"
    r = await world.shop.put(
        f"{URL}/{o['public_id']}/lines",
        {"version": o["version"], "lines": [{"product_ref": "pb", "pieces": "1.0001"}]},
    )
    assert r.status_code == 422
    r = await world.shop.put(
        f"{URL}/{o['public_id']}/lines",
        {"version": o["version"], "lines": [{"product_ref": "pb", "pieces": "-1"}]},
    )
    assert r.status_code == 422


async def test_duplicate_and_unknown_products_rejected(world: World) -> None:
    o = await world.new_order()
    body = {
        "version": o["version"],
        "lines": [
            {"product_ref": "pb", "pieces": "1"},
            {"product_ref": "pb", "pieces": "1"},
            {"product_ref": "ghost", "pieces": "1"},
        ],
    }
    r = await world.shop.put(f"{URL}/{o['public_id']}/lines", body)
    codes = {e["code"] for e in r.json()["detail"]["errors"]}
    assert r.status_code == 422 and {"duplicate_product", "unknown_product"} <= codes


async def test_lines_need_price_type(world: World) -> None:
    o = (await world.shop.post(URL, {})).json()
    r = await world.shop.put(
        f"{URL}/{o['public_id']}/lines",
        {"version": 1, "lines": [{"product_ref": "pb", "pieces": "1"}]},
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "price_type_required"


# ------------------------------------------------------------ price enforcement


async def test_client_sent_price_is_ignored(world: World) -> None:
    o = await world.new_order()
    r = await world.shop.put(
        f"{URL}/{o['public_id']}/lines",
        {
            "version": o["version"],
            "lines": [{"product_ref": "pb", "pieces": "2", "price": 1, "line_sum": 2}],
        },
    )
    assert r.status_code == 200
    assert line(r.json(), "pb")["price"] == 500 and r.json()["totals"]["sum"] == 1000


async def test_price_follows_chosen_price_type_and_reprices(world: World) -> None:
    o = await world.draft([("pb", 0, 2)])
    assert line(o, "pb")["price"] == 500
    r = await world.shop.patch(
        f"{URL}/{o['public_id']}", {"version": o["version"], "price_type": "d0_4"}
    )
    assert line(r.json(), "pb")["price"] == 450 and r.json()["totals"]["sum"] == 900
    # a price change in SD is picked up on the next read
    from app.models import SdPrice, SdPriceType

    pt = await world.session.scalar(select(SdPriceType.id).where(SdPriceType.sd_id == "d0_4"))
    await world.session.execute(
        update(SdPrice)
        .where(SdPrice.price_type_id == pt, SdPrice.product_id == world.catalog["pb"].id)
        .values(price=400)
    )
    await world.session.commit()
    got = (await world.shop.get(f"{URL}/{o['public_id']}")).json()
    assert line(got, "pb")["price"] == 400 and got["totals"]["sum"] == 800


async def test_unpriced_inactive_hidden_products_cannot_be_added(
    world: World, session: AsyncSession
) -> None:
    o = await world.new_order()
    for ref, issue in (
        ("pnoprice", "no_price"),
        ("pzero", "no_price"),
        ("pinactive", "not_allowed"),
        ("phidden", "not_allowed"),
    ):
        r = await world.shop.put(
            f"{URL}/{o['public_id']}/lines",
            {"version": o["version"], "lines": [{"product_ref": ref, "pieces": "1"}]},
        )
        assert r.status_code == 422, ref
        assert issue in {e["code"] for e in r.json()["detail"]["errors"]}, ref


async def test_product_hidden_after_adding_blocks_submit_warning(
    world: World, session: AsyncSession
) -> None:
    o = await world.draft([("pb", 0, 1)])
    session.add(ProductSetting(product_id=world.catalog["pb"].id, hidden=True))
    await session.commit()
    got = (await world.shop.get(f"{URL}/{o['public_id']}")).json()
    assert "not_allowed" in line(got, "pb")["warnings"]


async def test_product_filter_applies_to_lines(world: World, session: AsyncSession) -> None:
    await set_link(world, session, product_filter={"mode": "categories", "ids": ["c2"]})
    o = await world.new_order()
    r = await world.shop.put(
        f"{URL}/{o['public_id']}/lines",
        {"version": o["version"], "lines": [{"product_ref": "pa", "pieces": "1"}]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["code"] == "not_allowed"
    ok = await world.set_lines(o, [("pb", 0, 1)])
    assert ok["lines"][0]["product_ref"] == "pb"


# ------------------------------------------------------------- warnings / summaries


async def test_over_stock_is_a_line_warning_while_editing(world: World) -> None:
    o = await world.draft([("pb", 0, 9)])  # stock 5
    ln = line(o, "pb")
    assert ln["stock"] == 5 and "over_stock" in ln["warnings"]
    assert {p["code"] for p in o["problems"]} == {"over_stock"}


async def test_allow_over_stock_link_has_no_warning(world: World, session: AsyncSession) -> None:
    await set_link(world, session, allow_over_stock=True)
    o = await world.draft([("pb", 0, 9)])
    assert "over_stock" not in line(o, "pb")["warnings"] and o["problems"] == []


async def test_category_summary(world: World) -> None:
    o = await world.new_order()
    o = await world.set_lines(o, [("pa", 1, 0), ("pb", 0, 2), ("pc1", 0, 3)])
    cats = {c["category"]: c for c in o["categories"]}
    assert cats["c1"]["qty"] == 15 and cats["c1"]["sum"] == 12000 + 900
    assert cats["c1"]["volume"] == 6
    assert cats["c2"] == {
        "category": "c2", "name": "Cat 2", "qty": 2, "volume": 0.5, "sum": 1000,
    }  # fmt: skip
    assert o["totals"]["sum"] == 13900 and o["totals"]["lines_count"] == 3


# ----------------------------------------------------------------- line endpoints


async def test_line_patch_delete_and_version_bumps(world: World) -> None:
    o = await world.new_order()
    v = o["version"]
    r = await world.shop.patch(f"{URL}/{o['public_id']}/lines/pb", {"version": v, "pieces": "3"})
    assert r.status_code == 200 and r.json()["version"] == v + 1
    assert line(r.json(), "pb")["quantity"] == 3
    r = await world.shop.patch(
        f"{URL}/{o['public_id']}/lines/pb", {"version": v + 1, "pieces": "4", "comment": "hi"}
    )
    assert line(r.json(), "pb")["quantity"] == 4 and line(r.json(), "pb")["comment"] == "hi"
    r = await world.shop.patch(
        f"{URL}/{o['public_id']}/lines/pb", {"version": v + 2, "pieces": "0"}
    )
    assert r.json()["lines"] == []  # zero removes the line
    await world.shop.patch(f"{URL}/{o['public_id']}/lines/pb", {"version": v + 3, "pieces": "1"})
    r = await world.shop.delete(f"{URL}/{o['public_id']}/lines/pb", params={"version": v + 4})
    assert r.status_code == 200 and r.json()["lines"] == []
    r = await world.shop.delete(f"{URL}/{o['public_id']}/lines/pb", params={"version": v + 5})
    assert r.status_code == 404


async def test_stale_version_is_409(world: World) -> None:
    o = await world.draft()
    for r in (
        await world.shop.patch(f"{URL}/{o['public_id']}", {"version": 1, "comment": "x"}),
        await world.shop.put(f"{URL}/{o['public_id']}/lines", {"version": 1, "lines": []}),
        await world.shop.patch(f"{URL}/{o['public_id']}/lines/pb", {"version": 1, "pieces": "1"}),
        await world.shop.post(
            f"{URL}/{o['public_id']}/lines/scan", {"version": 1, "product_ref": "pb"}
        ),
        await world.shop.delete(f"{URL}/{o['public_id']}/lines/pb", params={"version": 1}),
        await world.shop.delete(f"{URL}/{o['public_id']}", params={"version": 1}),
        await world.submit(o, json={"version": 1}),
    ):
        assert r.status_code == 409
        assert r.json()["detail"] == {"code": "version_conflict", "current_version": o["version"]}


async def test_scan_adds_one_piece_each_time(world: World) -> None:
    o = await world.new_order()
    v = o["version"]
    r = await world.shop.post(
        f"{URL}/{o['public_id']}/lines/scan", {"version": v, "barcode": "4780000000028"}
    )
    assert r.status_code == 200 and line(r.json(), "pb")["quantity"] == 1
    r = await world.shop.post(
        f"{URL}/{o['public_id']}/lines/scan", {"version": v + 1, "barcode": "4780000000028"}
    )
    assert line(r.json(), "pb")["quantity"] == 2
    r = await world.shop.post(
        f"{URL}/{o['public_id']}/lines/scan", {"version": v + 2, "product_ref": "pa", "pieces": 5}
    )
    assert line(r.json(), "pa")["quantity"] == 5


async def test_scan_errors(world: World) -> None:
    o = await world.new_order()
    v = o["version"]
    url = f"{URL}/{o['public_id']}/lines/scan"
    r = await world.shop.post(url, {"version": v, "barcode": "4780000000035"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ambiguous_barcode"
    assert {c["product_ref"] for c in r.json()["detail"]["candidates"]} == {"pc1", "pc2"}
    r = await world.shop.post(url, {"version": v, "barcode": "0000000000000"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "barcode_not_found"
    r = await world.shop.post(url, {"version": v, "barcode": "4780000000073"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_orderable"
    assert (await world.shop.post(url, {"version": v})).status_code == 422


# ------------------------------------------------------------------ list/dup/delete


async def test_list_filters_and_pagination(world: World) -> None:
    a = await world.draft()
    await world.new_order()
    body = (await world.shop.get(URL)).json()
    assert body["total"] == 2 and body["items"][0]["created_at"] >= body["items"][1]["created_at"]
    assert (await world.shop.get(URL, params={"state": "sent"})).json()["total"] == 0
    assert (await world.shop.get(URL, params={"state": "bogus"})).status_code == 422
    q = (await world.shop.get(URL, params={"q": a["code"]})).json()
    assert [i["public_id"] for i in q["items"]] == [a["public_id"]]
    assert len((await world.shop.get(URL, params={"size": 1})).json()["items"]) == 1
    assert "lines" not in body["items"][0]


async def test_duplicate_copies_lines_and_header(world: World, session: AsyncSession) -> None:
    src = await world.draft([("pa", 1, 2), ("pb", 0, 1)])
    r = await world.shop.post(f"{URL}/{src['public_id']}/duplicate")
    assert r.status_code == 201
    new = r.json()
    assert new["public_id"] != src["public_id"] and new["state"] == "draft"
    assert new["price_type"]["id"] == "d0_2"
    assert {ln["product_ref"]: ln["quantity"] for ln in new["lines"]} == {"pa": 14, "pb": 1}
    # a product that became unorderable is skipped, not fatal
    session.add(ProductSetting(product_id=world.catalog["pb"].id, hidden=True))
    await session.commit()
    again = (await world.shop.post(f"{URL}/{src['public_id']}/duplicate")).json()
    assert [ln["product_ref"] for ln in again["lines"]] == ["pa"]


async def test_delete_draft(world: World, session: AsyncSession) -> None:
    o = await world.draft()
    assert (await world.shop.delete(f"{URL}/{o['public_id']}")).status_code == 204
    assert (await world.shop.get(f"{URL}/{o['public_id']}")).status_code == 404
    assert await session.scalar(select(ShopOrder).where(ShopOrder.code_1c == o["code"])) is None


async def test_sent_order_is_not_editable_or_deletable(world: World) -> None:
    o = await world.draft()
    assert (await world.submit(o)).status_code == 200
    got = (await world.shop.get(f"{URL}/{o['public_id']}")).json()
    assert got["state"] == "sent" and got["editable"] is False
    for r in (
        await world.shop.patch(
            f"{URL}/{o['public_id']}", {"version": got["version"], "comment": "x"}
        ),
        await world.shop.put(
            f"{URL}/{o['public_id']}/lines", {"version": got["version"], "lines": []}
        ),
        await world.shop.delete(f"{URL}/{o['public_id']}"),
    ):
        assert r.status_code == 409 and r.json()["detail"]["code"] == "order_not_editable"


# ------------------------------------------------------------------ access control


async def test_other_clients_order_is_404(
    world: World, app_factory: ApiFactory, session: AsyncSession
) -> None:
    o = await world.draft()
    other = await world.seed.client("Other", ["901112233"], sd_id="w9")
    assert other.id != world.client.id
    api = await app_factory(make_settings(), sd=world.sd, jobs=world.jobs)
    assert (await api.shop_login(SLUG, "998901112233")).status_code == 200
    base = f"{URL}/{o['public_id']}"
    assert (await api.get(base)).status_code == 404
    assert (await api.patch(base, {"version": 1, "comment": "x"})).status_code == 404
    assert (await api.put(f"{base}/lines", {"version": 1, "lines": []})).status_code == 404
    assert (await api.post(f"{base}/submit")).status_code == 404
    assert (await api.post(f"{base}/cancel")).status_code == 404
    assert (await api.post(f"{base}/duplicate")).status_code == 404
    assert (await api.delete(base)).status_code == 404
    assert (await api.get(URL)).json()["total"] == 0
    assert world.sd_server.calls == []


async def test_orders_require_login_and_csrf(world: World, app_factory: ApiFactory) -> None:
    anon = await app_factory(make_settings())
    assert (await anon.get(URL)).status_code == 401
    assert (await anon.post(URL, {})).status_code == 401
    r = await world.shop.post(URL, {}, csrf=False)
    assert r.status_code == 403


async def test_session_of_blocked_client_cannot_order(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(SdClient).where(SdClient.id == world.client.id).values(active=False)
    )
    await session.commit()
    world.redis_flush = None  # type: ignore[attr-defined]
    await world.redis.flushall()  # drop the 60 s revalidation cache
    assert (await world.shop.post(URL, {})).status_code == 401
    assert KEY  # keep import used

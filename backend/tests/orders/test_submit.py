"""Submit / cancel flow against the fake SD: success, validation, fresh stock, outcome handling."""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessLink, AuditLog, ShopOrder, ShopOrderEvent
from app.services.jobs import RECONCILE_ORDER, REFRESH_WAREHOUSE_STOCK
from tests.orders.conftest import WH, World

URL = "/api/shop/orders"


async def db_order(w: World, code: str) -> ShopOrder:
    await w.session.commit()  # never roll back: the shared test connection nests savepoints
    stmt = select(ShopOrder).where(ShopOrder.code_1c == code)
    return (await w.session.scalars(stmt.execution_options(populate_existing=True))).one()


async def events(w: World, order_id: int) -> list[str]:
    rows = await w.session.scalars(
        select(ShopOrderEvent.event)
        .where(ShopOrderEvent.order_id == order_id)
        .order_by(ShopOrderEvent.id)
    )
    return list(rows.all())


async def test_submit_success_sends_exact_payload(world: World) -> None:
    o = await world.draft([("pa", 1, 2), ("pb", 0, 1)])
    r = await world.submit(o)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "sent" and body["sd_status"] == 1 and body["sd_id"] == "d0_53000"
    (sent,) = world.sd_server.set_orders_sent()
    assert sent["code_1C"] == o["code"] and sent["status"] == 1 and sent["comment"] == ""
    assert sent["client"] == {"SD_id": "w3_7448"} and sent["agent"] == {"SD_id": "d0_11"}
    assert sent["priceType"] == {"SD_id": "d0_2"} and sent["warehouse"] == {"SD_id": WH}
    assert sent["trade"] == {"SD_id": 1}
    assert sent["orderProducts"] == [
        {"product": {"SD_id": "pa"}, "quantity": 14.0, "price": 1000.0, "discountSumma": 0.0},
        {"product": {"SD_id": "pb"}, "quantity": 1.0, "price": 500.0, "discountSumma": 0.0},
    ]


async def test_submit_updates_state_events_audit_and_jobs(
    world: World, session: AsyncSession
) -> None:
    o = await world.draft()
    assert (await world.submit(o)).status_code == 200
    row = await db_order(world, o["code"])
    assert (
        row.state == "sent"
        and row.sd_id
        and row.sent_at
        and row.submitted_at
        and row.last_error is None
    )
    assert await events(world, row.id) == ["created", "submitted", "sd_ok"]
    audit = (await session.scalars(select(AuditLog).where(AuditLog.action == "order.submit"))).one()
    assert audit.actor_type == "shop" and audit.entity_id == str(row.id)
    assert (REFRESH_WAREHOUSE_STOCK, (WH,), 2) in world.jobs.enqueued


async def test_submit_uses_link_order_status(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(AccessLink).values(settings={**world.link.settings, "order_status": 2})
    )
    await session.commit()
    o = await world.draft()
    body = (await world.submit(o)).json()
    assert world.sd_server.set_orders_sent()[0]["status"] == 2 and body["sd_status"] == 2
    assert body["state"] == "sent"


async def test_submit_stock_in_sd_decreases(world: World) -> None:
    o = await world.draft([("pb", 0, 2)])
    await world.submit(o)
    assert world.sd_server.stock[WH]["pb"] == 3
    # the local mirror is refreshed by the queued job, not inline
    assert (REFRESH_WAREHOUSE_STOCK, (WH,), 2) in world.jobs.enqueued


async def test_submit_empty_or_incomplete_order_is_422(world: World) -> None:
    o = await world.new_order()
    r = await world.submit(o)
    assert r.status_code == 422
    assert {e["code"] for e in r.json()["detail"]["errors"]} == {"no_lines"}
    bare = (await world.shop.post(URL, {})).json()
    r = await world.submit(bare)
    codes = {(e.get("field"), e["code"]) for e in r.json()["detail"]["errors"]}
    assert ("price_type", "required") in codes and (None, "no_lines") in codes
    assert world.sd_server.calls == []


async def test_over_stock_uses_fresh_stock_not_mirror(world: World) -> None:
    o = await world.draft([("pb", 0, 4)])  # mirror says 5 -> fine while editing
    assert o["problems"] == []
    world.sd_server.stock[WH]["pb"] = 3  # sold elsewhere meanwhile
    r = await world.submit(o)
    assert r.status_code == 422
    (err,) = r.json()["detail"]["errors"]
    assert err["code"] == "over_stock" and err["product_ref"] == "pb"
    assert err["available"] == 3 and err["requested"] == 4
    assert world.sd_server.calls_of("setOrder") == []
    row = await db_order(world, o["code"])
    assert row.state == "draft"  # back to editable
    # the mirror learned the fresh value
    stock = await world.shop.get("/api/shop/stock", params={"warehouse": WH, "products": "pb"})
    assert stock.json()["items"][0]["quantity"] == 3


async def test_fresh_stock_allows_what_stale_mirror_forbids(
    world: World, session: AsyncSession
) -> None:
    o = await world.draft([("pb", 0, 5)])
    world.sd_server.stock[WH]["pb"] = 50  # replenished in SD
    from app.models import SdStock

    await session.execute(
        update(SdStock).where(SdStock.product_id == world.catalog["pb"].id).values(quantity=1)
    )
    await session.commit()
    assert (await world.submit(o)).status_code == 200


async def test_allow_over_stock_link_submits(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(AccessLink).values(settings={**world.link.settings, "allow_over_stock": True})
    )
    await session.commit()
    o = await world.draft([("pb", 0, 9)])
    assert (await world.submit(o)).status_code == 200


async def test_min_max_order_sum(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(AccessLink).values(
            settings={**world.link.settings, "min_order_sum": 1000, "max_order_sum": 2000}
        )
    )
    await session.commit()
    low = await world.draft([("pb", 0, 1)])  # 500
    r = await world.submit(low)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "min_order_sum"
    high = await world.shop.post(f"{URL}/{low['public_id']}/duplicate")
    h = high.json()
    h = await world.set_lines(h, [("pa", 0, 3)])  # 3000
    r = await world.submit(h)
    assert r.json()["detail"]["errors"][0]["code"] == "max_order_sum"
    ok = await world.set_lines(h, [("pa", 0, 2)])  # 2000 == max
    assert (await world.submit(ok)).status_code == 200


async def test_hidden_after_adding_blocks_submit(world: World, session: AsyncSession) -> None:
    from app.models import ProductSetting

    o = await world.draft([("pb", 0, 1)])
    session.add(ProductSetting(product_id=world.catalog["pb"].id, hidden=True))
    await session.commit()
    r = await world.submit(o)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "not_allowed"
    assert world.sd_server.calls == []


async def test_submit_price_is_recomputed_not_stored_stale(
    world: World, session: AsyncSession
) -> None:
    from app.models import SdPrice

    o = await world.draft([("pb", 0, 2)])
    await session.execute(
        update(SdPrice).where(SdPrice.product_id == world.catalog["pb"].id).values(price=777)
    )
    await session.commit()
    assert (await world.submit(o)).status_code == 200
    assert world.sd_server.set_orders_sent()[0]["orderProducts"][0]["price"] == 777.0


# -------------------------------------------------------------------- concurrency


async def test_double_submit_sequential_makes_one_sd_call(world: World) -> None:
    o = await world.draft()
    assert (await world.submit(o)).status_code == 200
    r = await world.submit(o)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invalid_state"
    assert len(world.sd_server.calls_of("setOrder")) == 1


async def test_concurrent_double_submit_makes_one_sd_call(world: World) -> None:
    o = await world.draft()
    second: list[Any] = []

    async def fire_second() -> None:  # while the first request waits on SD (lock held)
        second.append(await world.submit(o))

    world.sd_server.before_set_order = fire_second
    first = await world.submit(o)
    assert first.status_code == 200
    assert second[0].status_code == 409 and second[0].json()["detail"]["code"] == "order_busy"
    assert len(world.sd_server.calls_of("setOrder")) == 1
    assert len(world.sd_server.orders) == 1


async def test_submit_when_lock_held_elsewhere(world: World) -> None:
    o = await world.draft()
    row = await db_order(world, o["code"])
    await world.redis.set(f"order:lock:{row.id}", "x")
    r = await world.submit(o)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "order_busy"
    assert world.sd_server.calls == []


async def test_submit_rate_limited(world: World, app_factory: Any) -> None:
    from tests.api.conftest import make_settings
    from tests.orders.conftest import PHONE, SLUG

    api = await app_factory(make_settings(RL_SHOP_SUBMIT_PER_MIN=1), sd=world.sd, jobs=world.jobs)
    await api.shop_login(SLUG, PHONE)
    o = (await api.post(URL, {})).json()
    assert (await api.post(f"{URL}/{o['public_id']}/submit")).status_code == 422
    assert (await api.post(f"{URL}/{o['public_id']}/submit")).status_code == 429


# -------------------------------------------------------------- SD failure mapping


async def test_sd_validation_error_maps_to_line_and_order_failed(
    world: World, session: AsyncSession
) -> None:
    o = await world.draft([("pa", 0, 1), ("pb", 0, 1)])
    world.sd_server.script = ["validation"]
    r = await world.submit(o)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "sd_rejected" and detail["state"] == "failed"
    assert detail["errors"][0]["product_ref"] == "pa"  # row index 0 -> first line
    assert detail["errors"][0]["message"] == "Product is blocked"
    row = await db_order(world, o["code"])
    assert row.state == "failed" and row.last_error and row.last_error["code"] == "sd_rejected"
    assert await events(world, row.id) == ["created", "submitted", "sd_error"]
    assert (
        await session.scalars(select(AuditLog).where(AuditLog.action == "order.submit_rejected"))
    ).one()
    assert RECONCILE_ORDER not in world.jobs.names()


async def test_failed_order_is_editable_and_resubmittable(world: World) -> None:
    o = await world.draft([("pa", 0, 1)])
    world.sd_server.script = ["validation"]
    assert (await world.submit(o)).status_code == 422
    got = (await world.shop.get(f"{URL}/{o['public_id']}")).json()
    assert got["state"] == "failed" and got["editable"] is True
    fixed = await world.set_lines(got, [("pb", 0, 1)])
    r = await world.submit(fixed)
    assert r.status_code == 200 and r.json()["state"] == "sent"
    assert len(world.sd_server.orders) == 1  # same code_1C throughout


async def test_sd_row_errors_in_200_response_are_rejections(world: World) -> None:
    o = await world.draft()
    world.sd_server.script = [
        {
            "status": True,
            "result": {
                "completed": 0,
                "error": 1,
                "data": {"order": [{"index": 0, "code_1C": o["code"], "errors": ["Bad agent"]}]},
            },
        }
    ]
    r = await world.submit(o)
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["message"] == "Bad agent"
    assert (await db_order(world, o["code"])).state == "failed"


async def test_sd_unreachable_marks_failed_and_retry_works(world: World) -> None:
    o = await world.draft()
    world.sd_server.script = ["connect_error", "connect_error"]  # initial + 1 retry
    r = await world.submit(o)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "sd_unavailable"
    assert (await db_order(world, o["code"])).state == "failed"
    assert (await world.submit(o)).status_code == 200


async def test_sd_stock_read_failure_reverts_to_draft(world: World) -> None:
    o = await world.draft()
    world.sd_server.stock_down = True
    r = await world.submit(o)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "sd_unavailable"
    assert (await db_order(world, o["code"])).state == "draft"
    assert world.sd_server.calls_of("setOrder") == []


async def test_outcome_unknown_schedules_reconcile(world: World) -> None:
    o = await world.draft()
    world.sd_server.script = ["timeout"]
    r = await world.submit(o)
    assert r.status_code == 202 and r.json()["state"] == "outcome_unknown"
    row = await db_order(world, o["code"])
    assert row.state == "outcome_unknown" and (row.last_error or {})["code"] == "outcome_unknown"
    assert [e for e in world.jobs.enqueued if e[0] == RECONCILE_ORDER] == [
        (RECONCILE_ORDER, (row.id,), 120.0)
    ]
    assert (await events(world, row.id))[-1] == "outcome_unknown"


async def test_unknown_order_is_not_editable_by_shop(world: World) -> None:
    o = await world.draft()
    world.sd_server.script = ["timeout"]
    await world.submit(o)
    r = await world.shop.put(f"{URL}/{o['public_id']}/lines", {"version": 99, "lines": []})
    assert r.status_code == 409


# ----------------------------------------------------------------------------- cancel


async def test_cancel_sent_status_1_sends_status_5(world: World) -> None:
    o = await world.draft([("pb", 0, 2)])
    await world.submit(o)
    assert world.sd_server.stock[WH]["pb"] == 3
    r = await world.shop.post(f"{URL}/{o['public_id']}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "cancelled" and body["sd_status"] == 5
    sent = world.sd_server.set_orders_sent()
    assert [s["status"] for s in sent] == [1, 5] and sent[0]["code_1C"] == sent[1]["code_1C"]
    assert world.sd_server.stock[WH]["pb"] == 5  # restored by SD
    row = await db_order(world, o["code"])
    assert await events(world, row.id) == ["created", "submitted", "sd_ok", "cancelled"]


async def test_cancel_rules(world: World) -> None:
    draft = await world.draft()
    r = await world.shop.post(f"{URL}/{draft['public_id']}/cancel")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_cancellable"
    sent = await world.draft([("pb", 0, 1)])
    await world.submit(sent)
    assert (await world.shop.post(f"{URL}/{sent['public_id']}/cancel")).status_code == 200
    again = await world.shop.post(f"{URL}/{sent['public_id']}/cancel")
    assert again.status_code == 409  # already cancelled


async def test_cancel_refused_when_sd_status_moved_on(world: World) -> None:
    o = await world.draft([("pb", 0, 1)])
    await world.submit(o)
    world.sd_server.orders[o["code"]]["status"] = 2  # dealer shipped it
    r = await world.shop.post(f"{URL}/{o['public_id']}/cancel")
    assert r.status_code == 409 and r.json()["detail"]["sd_status"] == 2
    got = (await world.shop.get(f"{URL}/{o['public_id']}")).json()
    assert got["sd_status"] == 2 and got["state"] == "sent"
    assert len(world.sd_server.set_orders_sent()) == 1  # no setOrder(status 5)


async def test_cancel_refused_for_local_status_2(world: World, session: AsyncSession) -> None:
    await session.execute(
        update(AccessLink).values(settings={**world.link.settings, "order_status": 2})
    )
    await session.commit()
    o = await world.draft([("pb", 0, 1)])
    await world.submit(o)
    r = await world.shop.post(f"{URL}/{o['public_id']}/cancel")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_cancellable"
    assert len(world.sd_server.calls) == 2  # getStock + setOrder only


async def test_cancel_sd_unreachable_keeps_order_sent(world: World) -> None:
    o = await world.draft([("pb", 0, 1)])
    await world.submit(o)
    world.sd_server.orders_down = True
    r = await world.shop.post(f"{URL}/{o['public_id']}/cancel")
    assert r.status_code == 503
    assert (await world.shop.get(f"{URL}/{o['public_id']}")).json()["state"] == "sent"


async def test_submit_writes_fresh_stock_only_for_its_own_products(world: World) -> None:
    """Load test finding: upserting the whole warehouse inside the submit transaction (open
    across the SD call) locked every sd_stock row and serialised concurrent submits."""
    from app.models import SdStock

    world.sd_server.stock[WH]["pa"] = 7  # SD differs for a product NOT in this order
    world.sd_server.stock[WH]["pb"] = 4  # ... and for the ordered one (mirror says 5)
    order = await world.draft([("pb", 0, 2)])
    r = await world.submit(order)
    assert r.status_code == 200, r.text

    async def mirror(ref: str) -> float:
        q = await world.session.scalar(
            select(SdStock.quantity)
            .where(SdStock.product_id == world.catalog[ref].id)
            .execution_options(populate_existing=True)
        )
        return float(q or 0)

    assert await mirror("pa") == 100  # untouched (refreshed later by the warehouse job)
    assert await mirror("pb") == 4  # fresh value from SD written for the order's own line
    assert REFRESH_WAREHOUSE_STOCK in world.jobs.names()

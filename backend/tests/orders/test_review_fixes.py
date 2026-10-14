"""Regression tests for the order-flow review (H1-H5, M1-M4, L1-L3)."""

import io
import threading
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models import AccessLink, AuditLog, SdPrice, SdPriceType, ShopOrder, ShopOrderEvent
from app.services import order_import, order_jobs
from app.services.jobs import RECONCILE_ORDER
from app.services.order_flow import LOCK_TTL, Services, order_lock, reconcile_order
from app.services.orders import bump_version
from app.sync.common import SD_TZ
from tests.orders.conftest import WH, World

A = "/api/admin/orders"
S = "/api/shop/orders"


async def load(w: World, code: str) -> ShopOrder:
    await w.session.commit()  # never roll back the shared test connection
    stmt = select(ShopOrder).where(ShopOrder.code_1c == code)
    return (await w.session.scalars(stmt.execution_options(populate_existing=True))).one()


async def force(w: World, code: str, **values: Any) -> None:
    await w.session.execute(update(ShopOrder).where(ShopOrder.code_1c == code).values(**values))
    await w.session.commit()


async def sent(w: World, lines: list[tuple[str, int, int | str]] | None = None) -> dict[str, Any]:
    o = await w.draft(lines or [("pb", 0, 2)])
    assert (await w.submit(o)).status_code == 200
    got: dict[str, Any] = (await w.shop.get(f"{S}/{o['public_id']}")).json()
    return got


async def age(w: World, code: str, minutes: int = 10) -> None:
    await force(w, code, submitted_at=clock.utcnow() - timedelta(minutes=minutes))


async def reconcile(w: World, order: ShopOrder, *, force_run: bool = True) -> str:
    async with w.sessionmaker() as db:
        return await reconcile_order(db, Services(w.sd, w.redis, w.jobs), order.id, force=force_run)


async def events(w: World, order_id: int) -> list[ShopOrderEvent]:
    await w.session.commit()
    rows = await w.session.scalars(
        select(ShopOrderEvent)
        .where(ShopOrderEvent.order_id == order_id)
        .order_by(ShopOrderEvent.id)
    )
    return list(rows.all())


def sd_qty(w: World, code: str, ref: str) -> float:
    return next(
        float(ln["quantity"])
        for ln in w.sd_server.orders[code]["orderProducts"]
        if ln["product"]["SD_id"] == ref
    )


async def edit_qty(w: World, o: dict[str, Any], pieces: str, **extra: Any) -> Any:
    return await w.admin.patch(
        f"{A}/{o['public_id']}",
        {
            "version": o["version"],
            "lines": [{"product_ref": "pb", "pieces": pieces}],
            **extra,
        },
    )


# ============================================================ H1: operation-aware reconcile


async def test_h1_unknown_admin_edit_records_pending_op(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    world.sd_server.script = ["timeout"]
    r = await edit_qty(world, o, "4")
    assert r.status_code == 202
    row = await load(world, o["code"])
    op = (row.last_error or {})["op"]
    assert op["name"] == "admin_edit" and op["status"] == 1 and len(op["payload_hash"]) == 16
    assert sd_qty(world, o["code"], "pb") == 2.0  # SD still has the OLD lines


async def test_h1_reconcile_resends_new_lines_when_sd_has_old_content(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    world.sd_server.script = ["timeout"]  # the edit never reached SD
    await edit_qty(world, o, "4")
    row = await load(world, o["code"])
    sent_before = len(world.sd_server.calls_of("setOrder"))
    assert await reconcile(world, row) == "sent"
    # found in SD by code_1C, but with stale lines -> the CURRENT payload was resent
    assert len(world.sd_server.calls_of("setOrder")) == sent_before + 1
    assert sd_qty(world, o["code"], "pb") == 4.0 and len(world.sd_server.orders) == 1
    assert (await load(world, o["code"])).state == "sent"
    assert ((await events(world, row.id))[-1].payload or {})["via"] == "resend"


async def test_h1_reconcile_does_not_resend_when_lines_match(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    world.sd_server.script = ["timeout_applied"]  # landed, response lost
    await edit_qty(world, o, "4")
    row = await load(world, o["code"])
    sent_before = len(world.sd_server.calls_of("setOrder"))
    assert await reconcile(world, row) == "sent"
    assert len(world.sd_server.calls_of("setOrder")) == sent_before  # verified, not resent
    assert ((await events(world, row.id))[-1].payload or {})["via"] == "lookup"


async def test_h1_reconcile_never_overwrites_a_shipped_order(
    world: World, session: AsyncSession
) -> None:
    o = await sent(world, [("pb", 0, 2)])
    world.sd_server.script = ["timeout"]
    await edit_qty(world, o, "4")
    world.sd_server.orders[o["code"]]["status"] = 2  # dealer shipped it meanwhile
    row = await load(world, o["code"])
    before = len(world.sd_server.calls_of("setOrder"))
    assert await reconcile(world, row) == "sent"
    assert len(world.sd_server.calls_of("setOrder")) == before  # nothing resent over a shipment
    done = await load(world, o["code"])
    assert done.state == "sent" and done.sd_status == 2
    assert sd_qty(world, o["code"], "pb") == 2.0
    ev = (await events(world, row.id))[-1]
    assert ev.event == "reconciled" and (ev.payload or {})["diverged"] is True
    alert = await session.scalar(
        select(AuditLog).where(AuditLog.action == "order.reconcile_diverged")
    )
    assert alert is not None


async def test_h1_unknown_submit_resends_when_missing_in_sd(world: World) -> None:
    o = await world.draft([("pb", 0, 1)])
    world.sd_server.script = ["timeout"]
    await world.submit(o)
    row = await load(world, o["code"])
    assert (row.last_error or {})["op"]["name"] == "submit"
    assert await reconcile(world, row) == "sent"
    assert sd_qty(world, o["code"], "pb") == 1.0


# ===================================================== H2: one lock, re-read, guarded version


async def test_h2_admin_edit_holds_the_order_lock(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    seen: dict[str, Any] = {}

    async def during_edit_send() -> None:  # the admin edit is talking to SD right now
        seen["cancel"] = await world.shop.post(f"{S}/{o['public_id']}/cancel")
        seen["status"] = await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 2})
        seen["delete"] = await world.admin.delete(f"{A}/{o['public_id']}")
        seen["edit"] = await edit_qty(world, o, "3")

    world.sd_server.before_set_order = during_edit_send
    r = await edit_qty(world, o, "4")
    assert r.status_code == 200
    for name, resp in seen.items():
        assert resp.status_code == 409, name
        assert resp.json()["detail"]["code"] == "order_busy", name
    assert sd_qty(world, o["code"], "pb") == 4.0
    assert world.sd_server.deleted == [] and world.sd_server.calls_of("setStatus") == []


async def test_h2_admin_status_blocks_concurrent_edit(world: World) -> None:
    o = await sent(world)
    seen: dict[str, Any] = {}

    async def during_set_status() -> None:
        seen["edit"] = await edit_qty(world, o, "3")

    world.sd_server.hooks["setStatus"] = during_set_status
    r = await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 2})
    assert r.status_code == 200
    assert seen["edit"].status_code == 409 and seen["edit"].json()["detail"]["code"] == "order_busy"


async def test_h2_cancel_blocks_concurrent_status_and_edit(world: World) -> None:
    o = await sent(world)
    seen: dict[str, Any] = {}

    async def during_cancel_send() -> None:
        seen["status"] = await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 2})
        seen["edit"] = await edit_qty(world, o, "3")

    world.sd_server.before_set_order = during_cancel_send
    assert (await world.shop.post(f"{S}/{o['public_id']}/cancel")).status_code == 200
    assert {r.json()["detail"]["code"] for r in seen.values()} == {"order_busy"}
    assert world.sd_server.calls_of("setStatus") == []


async def test_h2_state_and_version_rechecked_after_lock(world: World) -> None:
    o = await sent(world)
    stale = o["version"]
    assert (
        await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 2})
    ).status_code == 200
    r = await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 3, "version": stale})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_conflict"
    r = await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 5})
    assert r.status_code == 200  # fresh read: still "sent", so allowed


async def test_h2_version_bump_is_guarded_in_the_update(
    world: World, session: AsyncSession
) -> None:
    o = await world.draft()
    row = await load(world, o["code"])
    loaded = row.version
    await session.execute(  # a concurrent writer got in first; leave our in-memory copy stale
        update(ShopOrder)
        .where(ShopOrder.id == row.id)
        .values(version=loaded + 5)
        .execution_options(synchronize_session=False)
    )
    with pytest.raises(HTTPException) as exc:
        await bump_version(session, row)
    detail: dict[str, Any] = exc.value.detail  # type: ignore[assignment]
    assert exc.value.status_code == 409 and detail["code"] == "version_conflict"
    assert detail["current_version"] == loaded + 5
    assert row.version == loaded  # nothing was bumped in memory either


async def test_h2_reconcile_and_status_sync_respect_the_lock(world: World) -> None:
    o = await sent(world)
    row = await load(world, o["code"])
    async with order_lock(world.redis, row.id):
        world.sd_server.orders[o["code"]]["status"] = 3
        stats = await order_jobs.sync_order_statuses(world.sync)
        assert stats["skipped_locked"] == 1
    assert (await load(world, o["code"])).sd_status == 1


# ==================================================== H3: exhausted / discard / own reservation


async def test_h3_exhausted_is_counted_on_dashboard(world: World) -> None:
    o = await world.draft([("pb", 0, 1)])
    world.sd_server.script = ["timeout"]
    await world.submit(o)
    row = await load(world, o["code"])
    world.sd_server.orders_down = True
    for _ in range(6):
        await age(world, o["code"])
        await reconcile(world, row, force_run=False)
    dash = (await world.admin.get("/api/admin/dashboard")).json()
    assert dash["reconcile_exhausted"] == 1 and dash["outcome_unknown"] == 1 and dash["failed"] == 0


async def test_h3_admin_delete_of_parked_order_cancels_in_sd(world: World) -> None:
    o = await world.draft([("pb", 0, 2)])
    world.sd_server.script = ["timeout_applied"]  # SD holds a status-1 reservation
    await world.submit(o)
    assert world.sd_server.stock[WH]["pb"] == 3
    row = await load(world, o["code"])
    await force(world, o["code"], last_error={**(row.last_error or {}), "exhausted": True})
    r = await world.admin.delete(f"{A}/{o['public_id']}")
    assert r.status_code == 200 and r.json()["state"] == "deleted"
    assert world.sd_server.orders[o["code"]]["status"] == 5 and world.sd_server.deleted == [
        o["code"]
    ]
    assert world.sd_server.stock[WH]["pb"] == 5  # reservation released, not leaked


async def test_h3_shop_delete_of_previously_submitted_order_goes_through_sd(
    world: World,
) -> None:
    o = await sent(world, [("pb", 0, 2)])
    await force(world, o["code"], state="failed", sd_status=None)  # looks failed, SD has it
    r = await world.shop.delete(f"{S}/{o['public_id']}")
    assert r.status_code == 204
    assert [s["status"] for s in world.sd_server.set_orders_sent()] == [1, 5]
    assert world.sd_server.deleted == [o["code"]] and world.sd_server.stock[WH]["pb"] == 5
    row = await load(world, o["code"])
    assert row.state == "deleted"  # kept (soft) for the audit trail
    assert (await world.shop.get(S)).json()["total"] == 0


async def test_h3_shop_delete_of_submitted_order_missing_in_sd_is_local(world: World) -> None:
    o = await world.draft([("pb", 0, 1)])
    world.sd_server.script = ["validation"]
    await world.submit(o)  # submitted_at is set, SD has nothing
    assert (await world.shop.delete(f"{S}/{o['public_id']}")).status_code == 204
    assert world.sd_server.deleted == [] and len(world.sd_server.orders) == 0
    assert (await load(world, o["code"])).state == "deleted"


async def test_h3_never_submitted_draft_is_hard_deleted_without_sd(world: World) -> None:
    o = await world.draft()
    assert (await world.shop.delete(f"{S}/{o['public_id']}")).status_code == 204
    await world.session.commit()
    assert (
        await world.session.scalar(select(ShopOrder).where(ShopOrder.code_1c == o["code"])) is None
    )
    assert world.sd_server.calls == []


async def test_h3_shop_delete_sd_unreachable_keeps_order(world: World) -> None:
    o = await sent(world)
    await force(world, o["code"], state="failed")
    world.sd_server.orders_down = True
    r = await world.shop.delete(f"{S}/{o['public_id']}")
    assert r.status_code == 502
    assert (await load(world, o["code"])).state == "failed" and world.sd_server.deleted == []


async def test_h3_resubmit_credits_own_sd_reservation(world: World) -> None:
    o = await sent(world, [("pb", 0, 5)])  # takes all 5 -> SD stock 0
    assert world.sd_server.stock[WH]["pb"] == 0
    await force(world, o["code"], state="failed", sd_status=None)
    got = (await world.shop.get(f"{S}/{o['public_id']}")).json()
    r = await world.shop.post(f"{S}/{o['public_id']}/submit", json={"version": got["version"]})
    assert r.status_code == 200, r.text  # without the credit this is "over_stock" (0 available)
    assert len(world.sd_server.orders) == 1 and r.json()["state"] == "sent"
    # but it is only a credit for what we already hold
    await force(world, o["code"], state="failed", sd_status=None)
    got = (await world.shop.get(f"{S}/{o['public_id']}")).json()
    bigger = await world.shop.put(
        f"{S}/{o['public_id']}/lines",
        {"version": got["version"], "lines": [{"product_ref": "pb", "pieces": "6"}]},
    )
    r = await world.shop.post(
        f"{S}/{o['public_id']}/submit", json={"version": bigger.json()["version"]}
    )
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "over_stock"


# ==================================================================== H4: stuck sweep


async def test_h4_sweep_recovers_stuck_submitting_unless_lock_held(world: World) -> None:
    a = await world.draft()
    b = await world.draft()
    for o in (a, b):
        await force(
            world, o["code"], state="submitting", submitted_at=clock.utcnow() - timedelta(minutes=9)
        )
    row_b = await load(world, b["code"])
    async with order_lock(world.redis, row_b.id):  # b is genuinely in flight
        await order_jobs.sweep_unknown(world.sync, world.jobs)
    assert (await load(world, a["code"])).state == "outcome_unknown"
    assert (await load(world, b["code"])).state == "submitting"
    await order_jobs.sweep_unknown(world.sync, world.jobs)  # lock gone -> now recovered
    assert (await load(world, b["code"])).state == "outcome_unknown"
    assert [n for n, _, _ in world.jobs.enqueued].count(RECONCILE_ORDER) >= 2


async def test_h4_sweep_is_wired_to_the_per_minute_cron() -> None:
    from app.workers import settings as worker

    assert any("sweep_unknown_orders" in c.name for c in worker.WorkerSettings.cron_jobs)


# ================================================================== H5: import hardening

EVIL = ["1E9999999", "1e5", "9" * 40, "0x10", "NaN", "Infinity", "-1E999999999", "1" + "0" * 15]


async def upload(w: World, order: dict[str, Any], name: str, content: bytes) -> Any:
    return await w.shop.http.post(
        f"{S}/import",
        data={"order_id": order["public_id"]},
        files={"file": (name, content, "application/octet-stream")},
        headers={"X-CSRF-Token": w.shop.csrf or ""},
    )


async def test_h5_exponent_and_huge_numbers_are_rejected_fast(world: World) -> None:
    order = await world.new_order()
    rows = "\n".join(f"4780000000028,{v}" for v in EVIL)
    started = time.perf_counter()
    r = await upload(world, order, "o.csv", f"barcode,qty\n{rows}".encode())
    assert time.perf_counter() - started < 5
    assert r.status_code == 200, r.text
    assert r.json()["matched"] == []
    assert {u["reason"] for u in r.json()["unmatched"]} == {"bad_number"}
    assert len(r.json()["unmatched"]) == len(EVIL)


async def test_h5_blocks_column_exponent_and_xlsx_cells(world: World) -> None:
    order = await world.new_order()
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["barcode", "blocks", "qty"])
    ws.append(["4780000000011", "1E9999999", 1])
    ws.append(["4780000000011", 1e300, 1])
    ws.append(["4780000000011", 0, 999999999])  # in range of the regex, over the quantity cap
    ws.append(["4780000000011", 0, 3])
    buf = io.BytesIO()
    wb.save(buf)
    r = await upload(world, order, "o.xlsx", buf.getvalue())
    assert r.status_code == 200, r.text
    reasons = {u["row"]: u["reason"] for u in r.json()["unmatched"]}
    assert reasons == {2: "bad_number", 3: "bad_number", 4: "quantity_too_large"}
    assert [m["quantity"] for m in r.json()["matched"]] == [3]


def test_h5_cell_text_survives_non_finite_floats() -> None:
    assert order_import._cell_text(float("inf")) == ""
    assert order_import._cell_text(float("nan")) == ""
    assert order_import._cell_text(4780000000028.0) == "4780000000028"
    with pytest.raises(ValueError, match="bad_number"):
        order_import._to_decimal("1E9999999")
    assert order_import._to_decimal("1,5") == Decimal("1.5")


async def test_h5_parsing_runs_off_the_event_loop(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = order_import.parse_upload

    def spy(filename: str, content: bytes) -> Any:
        seen.append(threading.current_thread().name)
        return real(filename, content)

    monkeypatch.setattr(order_import, "parse_upload", spy)
    order = await world.new_order()
    assert (await upload(world, order, "o.csv", b"barcode,qty\n4780000000028,1")).status_code == 200
    assert seen and seen[0] != threading.main_thread().name


# ================================================================ M1: status sync snapshot


async def test_m1_sync_skips_orders_changed_after_the_snapshot_started(world: World) -> None:
    o = await sent(world)
    world.sd_server.orders[o["code"]]["status"] = 2  # what the (old) snapshot will say

    async def local_cancel_lands_mid_snapshot() -> None:
        await world.session.execute(
            update(ShopOrder)
            .where(ShopOrder.code_1c == o["code"])
            .values(state="cancelled", sd_status=5, updated_at=clock.utcnow())
        )
        await world.session.commit()

    world.sd_server.hooks["getOrder"] = local_cancel_lands_mid_snapshot
    stats = await order_jobs.sync_order_statuses(world.sync)
    assert stats["skipped_fresh"] == 1 and stats["updated"] == 0
    row = await load(world, o["code"])
    assert row.state == "cancelled" and row.sd_status == 5  # not reverted by stale data


# ===================================================== M2: cancel live check maps the state


async def test_m2_cancel_live_check_maps_state_like_sync(world: World) -> None:
    o = await sent(world)
    world.sd_server.orders[o["code"]]["status"] = 5  # dealer already cancelled it
    r = await world.shop.post(f"{S}/{o['public_id']}/cancel")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "not_cancellable", "state": "cancelled", "sd_status": 5}
    row = await load(world, o["code"])
    assert row.state == "cancelled" and row.sd_status == 5
    shipped = await sent(world)
    world.sd_server.orders[shipped["code"]]["status"] = 3
    r = await world.shop.post(f"{S}/{shipped['public_id']}/cancel")
    assert r.json()["detail"]["state"] == "sent" and r.json()["detail"]["sd_status"] == 3


# ============================================ M3: delete vs concurrent resubmit (re-read)


async def test_m3_delete_wins_over_concurrent_resubmit(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    await force(world, o["code"], state="failed", sd_status=None)
    seen: dict[str, Any] = {}

    async def resubmit_during_delete() -> None:
        got = (await world.shop.get(f"{S}/{o['public_id']}")).json()
        seen["resubmit"] = await world.submit(got)

    world.sd_server.hooks["getOrder"] = resubmit_during_delete
    r = await world.admin.delete(f"{A}/{o['public_id']}")
    assert r.status_code == 200 and r.json()["state"] == "deleted"
    assert seen["resubmit"].status_code == 409
    assert seen["resubmit"].json()["detail"]["code"] == "order_busy"
    assert [s["status"] for s in world.sd_server.set_orders_sent()] == [1, 5]  # no resurrection


async def test_m3_delete_after_state_changed_is_rejected(world: World) -> None:
    o = await sent(world)
    assert (await world.admin.delete(f"{A}/{o['public_id']}")).status_code == 200
    again = await world.admin.delete(f"{A}/{o['public_id']}")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "invalid_state"


# ============================================================= M4: admin edit price handling


async def set_mirror_price(w: World, ref: str, price_type: str, price: int) -> None:
    pt = await w.session.scalar(select(SdPriceType.id).where(SdPriceType.sd_id == price_type))
    await w.session.execute(
        update(SdPrice)
        .where(SdPrice.price_type_id == pt, SdPrice.product_id == w.catalog[ref].id)
        .values(price=price)
    )
    await w.session.commit()


async def test_m4_unchanged_lines_keep_their_price(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])  # price 500
    await set_mirror_price(world, "pb", "d0_2", 700)
    r = await world.admin.patch(
        f"{A}/{o['public_id']}", {"version": o["version"], "comment": "note only"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lines"][0]["price"] == 500 and body["totals"]["sum"] == 1000
    assert body["repriced"] == []
    assert body["price_drift"] == [{"product_ref": "pb", "kept_price": 500, "current_price": 700}]
    assert world.sd_server.set_orders_sent()[-1]["orderProducts"][0]["price"] == 500.0


async def test_m4_quantity_change_keeps_price_new_lines_get_current(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    await set_mirror_price(world, "pb", "d0_2", 700)
    r = await world.admin.patch(
        f"{A}/{o['public_id']}",
        {
            "version": o["version"],
            "lines": [
                {"product_ref": "pb", "pieces": "3"},
                {"product_ref": "pc1", "pieces": "1"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    prices = {ln["product_ref"]: ln["price"] for ln in r.json()["lines"]}
    assert prices == {"pb": 500, "pc1": 300}


async def test_m4_explicit_reprice_flags_changed_lines(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    await set_mirror_price(world, "pb", "d0_2", 700)
    r = await world.admin.patch(f"{A}/{o['public_id']}", {"version": o["version"], "reprice": True})
    body = r.json()
    assert body["lines"][0]["price"] == 700 and body["totals"]["sum"] == 1400
    assert body["repriced"] == [{"product_ref": "pb", "old": 500, "new": 700}]
    assert body["price_drift"] == []
    assert world.sd_server.set_orders_sent()[-1]["orderProducts"][0]["price"] == 700.0


async def test_m4_price_type_change_reprices_everything(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    r = await world.admin.patch(
        f"{A}/{o['public_id']}", {"version": o["version"], "price_type": "d0_4"}
    )
    assert r.json()["lines"][0]["price"] == 450
    assert r.json()["repriced"] == [{"product_ref": "pb", "old": 500, "new": 450}]


# ================================================= L1: cancel unknown / delete partial


async def test_l1_cancel_unknown_outcome_is_marked_and_reconciled(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    world.sd_server.script = ["timeout"]  # status-5 write lost
    r = await world.shop.post(f"{S}/{o['public_id']}/cancel")
    assert r.status_code == 202 and r.json()["state"] == "outcome_unknown"
    row = await load(world, o["code"])
    assert (row.last_error or {})["op"]["name"] == "cancel"
    assert any(n == RECONCILE_ORDER for n, _, _ in world.jobs.enqueued)
    assert await reconcile(world, row) == "sent"  # found status 1 -> cancel resent
    done = await load(world, o["code"])
    assert done.state == "cancelled" and done.sd_status == 5
    assert world.sd_server.stock[WH]["pb"] == 5


async def test_l1_cancel_that_landed_is_verified_without_resending(world: World) -> None:
    o = await sent(world)
    world.sd_server.script = ["timeout_applied"]
    assert (await world.shop.post(f"{S}/{o['public_id']}/cancel")).status_code == 202
    row = await load(world, o["code"])
    before = len(world.sd_server.calls_of("setOrder"))
    assert await reconcile(world, row) == "sent"
    assert len(world.sd_server.calls_of("setOrder")) == before
    assert (await load(world, o["code"])).state == "cancelled"


async def test_l1_partial_delete_is_marked_for_verification(world: World) -> None:
    o = await sent(world, [("pb", 0, 2)])
    world.sd_server.delete_script = ["timeout"]  # cancel (status 5) ok, setDeletedOrder lost
    r = await world.admin.delete(f"{A}/{o['public_id']}")
    assert r.status_code == 202
    row = await load(world, o["code"])
    assert row.state == "outcome_unknown" and (row.last_error or {})["op"]["name"] == "delete"
    assert world.sd_server.orders[o["code"]]["status"] == 5
    assert await reconcile(world, row) == "sent"
    assert (await load(world, o["code"])).state == "deleted"
    assert world.sd_server.deleted == [o["code"]]  # the lost call never reached SD; retried once
    assert len(world.sd_server.calls_of("setDeletedOrder")) == 2


async def test_l1_unknown_status_change_is_reconciled(world: World) -> None:
    o = await sent(world)
    # setStatus is not scripted in the fake: make it time out via the hook
    import httpx

    async def boom() -> None:
        raise httpx.ReadTimeout("slow")

    world.sd_server.hooks["setStatus"] = boom
    r = await world.admin.post(f"{A}/{o['public_id']}/status", {"status": 2})
    assert r.status_code == 202
    row = await load(world, o["code"])
    assert (row.last_error or {})["op"] == {"name": "status", "status": 2, "previous": 1}
    assert await reconcile(world, row) == "sent"
    done = await load(world, o["code"])
    assert done.state == "sent" and done.sd_status == 2
    assert world.sd_server.orders[o["code"]]["status"] == 2


# =========================================================================== L2: lock TTL


async def test_l2_lock_ttl_is_long_and_renewed_between_sd_calls(world: World) -> None:
    assert LOCK_TTL == 600
    o = await world.draft([("pb", 0, 1)])
    row = await load(world, o["code"])
    key = f"order:lock:{row.id}"
    ttls: list[int] = []

    async def shrink_after_stock_read() -> None:
        await world.redis.expire(key, 5)  # pretend the stock call was very slow

    async def check_before_set_order() -> None:
        ttls.append(await world.redis.ttl(key))

    world.sd_server.hooks["getStock"] = shrink_after_stock_read
    world.sd_server.before_set_order = check_before_set_order
    # (hook runs at the START of getStock, so the renew before setOrder must restore the TTL)
    assert (await world.submit(o)).status_code == 200
    assert ttls and ttls[0] > 300


# ============================================================ L3: lookup window + old orders


async def test_l3_lookup_window_starts_at_first_send_not_creation(world: World) -> None:
    o = await sent(world)
    await force(world, o["code"], created_at=clock.utcnow() - timedelta(days=40))
    world.sd_server.calls.clear()
    assert (await world.shop.post(f"{S}/{o['public_id']}/cancel")).status_code == 200
    (call,) = world.sd_server.calls_of("getOrder")[:1]
    start = call["params"]["filter"]["period"]["dateCreate"]["from"]
    expected = (clock.utcnow().astimezone(SD_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    assert start.startswith(expected)


async def test_l3_hourly_sync_updates_old_open_orders(world: World) -> None:
    old = await sent(world, [("pb", 0, 1)])
    ancient = await sent(world, [("pa", 0, 1)])
    recent = await sent(world, [("pc1", 0, 1)])
    await force(world, old["code"], created_at=clock.utcnow() - timedelta(days=45))
    await force(world, ancient["code"], created_at=clock.utcnow() - timedelta(days=200))
    for o in (old, ancient, recent):
        world.sd_server.orders[o["code"]]["status"] = 2
    world.sd_server.calls.clear()
    stats = await order_jobs.sync_old_open_orders(world.sync)
    assert stats["updated"] == 1
    assert (await load(world, old["code"])).sd_status == 2
    assert (await load(world, ancient["code"])).sd_status == 1  # beyond the 180-day bound
    assert (await load(world, recent["code"])).sd_status == 1  # covered by the 5-minute sync
    (call,) = world.sd_server.calls_of("getOrder")
    flt = call["params"]["filter"]
    assert flt["status"] == [1, 2] and "period" in flt


async def test_l3_old_sync_is_scheduled_hourly() -> None:
    from app.workers import settings as worker

    assert any("sync_old_open_orders" in c.name for c in worker.WorkerSettings.cron_jobs)
    assert "sync_old_open_orders" in {f.__name__ for f in worker.WorkerSettings.functions}


# ==================================== admin bypasses the link's min/max, not stock rules


async def test_admin_bypasses_min_max_but_not_stock(world: World, session: AsyncSession) -> None:
    o = await sent(world, [("pb", 0, 2)])
    await session.execute(
        update(AccessLink).values(
            settings={**world.link.settings, "min_order_sum": 5000, "max_order_sum": 6000}
        )
    )
    await session.commit()
    r = await edit_qty(world, o, "1")  # 500 < min
    assert r.status_code == 200, r.text
    r = await world.admin.patch(
        f"{A}/{o['public_id']}",
        {"version": r.json()["version"], "lines": [{"product_ref": "pa", "pieces": "60"}]},
    )
    assert r.status_code == 200  # 60000 > max, fine for an admin
    over = await world.admin.patch(
        f"{A}/{o['public_id']}",
        {"version": r.json()["version"], "lines": [{"product_ref": "pb", "pieces": "9"}]},
    )
    assert over.status_code == 422 and over.json()["detail"]["errors"][0]["code"] == "over_stock"


async def test_admin_resend_of_failed_order_bypasses_min_max(
    world: World, session: AsyncSession
) -> None:
    await session.execute(
        update(AccessLink).values(settings={**world.link.settings, "min_order_sum": 5000})
    )
    await session.commit()
    o = await world.draft([("pb", 0, 1)])  # 500 < min: the shop itself cannot submit it
    assert (await world.submit(o)).status_code == 422
    await force(world, o["code"], state="failed")
    r = await world.admin.post(f"{A}/{o['public_id']}/resend")
    assert r.status_code == 200 and r.json()["state"] == "sent"

"""Background jobs: reconcile (idempotent), stuck-submitting recovery, status sync, sweeps."""

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models import AuditLog, SdStock, ShopOrder, ShopOrderEvent
from app.services import order_jobs
from app.services.jobs import RECONCILE_ORDER, REFRESH_WAREHOUSE_STOCK
from app.services.order_flow import RECONCILE_MAX_ATTEMPTS
from app.workers import settings as worker
from tests.orders.conftest import WH, World


async def unknown_order(w: World, script: str = "timeout") -> ShopOrder:
    o = await w.draft([("pb", 0, 2)])
    w.sd_server.script = [script]
    r = await w.submit(o)
    assert r.status_code == 202
    w.jobs.enqueued.clear()
    return await load(w, o["code"])


async def load(w: World, code: str) -> ShopOrder:
    # never roll the shared test connection back (it would undo other sessions' savepoints)
    await w.session.commit()
    stmt = select(ShopOrder).where(ShopOrder.code_1c == code)
    return (await w.session.scalars(stmt.execution_options(populate_existing=True))).one()


async def age(w: World, order: ShopOrder, minutes: int = 10) -> None:
    await w.session.execute(
        update(ShopOrder)
        .where(ShopOrder.id == order.id)
        .values(submitted_at=clock.utcnow() - timedelta(minutes=minutes))
    )
    await w.session.commit()


async def events(w: World, order: ShopOrder) -> list[str]:
    rows = await w.session.scalars(
        select(ShopOrderEvent.event)
        .where(ShopOrderEvent.order_id == order.id)
        .order_by(ShopOrderEvent.id)
    )
    return list(rows.all())


async def run(w: World, order: ShopOrder) -> str:
    return await order_jobs.reconcile_job(w.sync, w.jobs, order.id)


# ----------------------------------------------------------------------------- reconcile


async def test_reconcile_resends_when_sd_never_got_it(world: World) -> None:
    order = await unknown_order(world, "timeout")
    await age(world, order)
    assert await run(world, order) == "sent"
    row = await load(world, order.code_1c)
    assert row.state == "sent" and row.sd_id == "d0_53000" and row.last_error is None
    assert len(world.sd_server.calls_of("setOrder")) == 2  # lost attempt + idempotent resend
    assert len(world.sd_server.orders) == 1
    assert (await events(world, row))[-1] == "reconciled"
    assert (REFRESH_WAREHOUSE_STOCK, (WH,), 2) in world.jobs.enqueued


async def test_reconcile_finds_applied_order_without_resending(world: World) -> None:
    order = await unknown_order(world, "timeout_applied")
    assert len(world.sd_server.orders) == 1
    await age(world, order)
    assert await run(world, order) == "sent"
    assert len(world.sd_server.calls_of("setOrder")) == 1  # only the original attempt
    row = await load(world, order.code_1c)
    assert row.state == "sent" and row.sd_id == world.sd_server.orders[order.code_1c]["SD_id"]
    assert row.sd_status == 1


async def test_reconcile_is_idempotent(world: World) -> None:
    order = await unknown_order(world, "timeout")
    await age(world, order)
    assert await run(world, order) == "sent"
    assert await run(world, order) == "skipped"  # already sent
    assert len(world.sd_server.calls_of("setOrder")) == 2  # unchanged by the second run
    assert len(world.sd_server.orders) == 1


async def test_reconcile_adopts_status_found_in_sd(world: World) -> None:
    order = await unknown_order(world, "timeout_applied")
    world.sd_server.orders[order.code_1c]["status"] = 2
    await age(world, order)
    await run(world, order)
    assert (await load(world, order.code_1c)).sd_status == 2


async def test_reconcile_too_early_waits(world: World) -> None:
    order = await unknown_order(world)
    assert await run(world, order) == "skipped"
    assert (RECONCILE_ORDER, (order.id,), 30) in world.jobs.enqueued
    assert world.sd_server.calls == [] or not world.sd_server.calls_of("getOrder")


async def test_reconcile_force_ignores_age(world: World) -> None:
    from app.services.order_flow import Services, reconcile_order

    order = await unknown_order(world)
    async with world.sessionmaker() as db:
        out = await reconcile_order(
            db, Services(world.sd, world.redis, world.jobs), order.id, force=True
        )
    assert out == "sent"


async def test_reconcile_sd_down_backs_off_then_gives_up(
    world: World, session: AsyncSession
) -> None:
    order = await unknown_order(world)
    await age(world, order)
    world.sd_server.orders_down = True
    delays: list[float] = []
    for attempt in range(1, RECONCILE_MAX_ATTEMPTS):
        world.jobs.enqueued.clear()
        assert await run(world, order) == "pending"
        row = await load(world, order.code_1c)
        assert (
            row.state == "outcome_unknown"
            and (row.last_error or {})["reconcile_attempts"] == attempt
        )
        ((name, args, delay),) = world.jobs.enqueued
        assert (name, args) == (RECONCILE_ORDER, (order.id,))
        delays.append(delay or 0)
    assert delays == sorted(delays) and delays[-1] > delays[0]  # exponential backoff
    assert await run(world, order) == "exhausted"
    row = await load(world, order.code_1c)
    # parked, NOT failed: SD may still hold a reservation, so the state stays unverified
    assert row.state == "outcome_unknown" and (row.last_error or {})["exhausted"] is True
    alert = (
        await session.scalars(
            select(AuditLog).where(AuditLog.action == "order.reconcile_exhausted")
        )
    ).one()
    assert alert.actor_type == "system" and alert.entity_id == str(order.id)
    world.jobs.enqueued.clear()
    assert await run(world, order) == "skipped"  # no automatic retries any more
    assert await order_jobs.sweep_unknown(world.sync, world.jobs) == 0
    assert world.jobs.enqueued == []


async def test_reconcile_resend_rejected_marks_failed(world: World) -> None:
    order = await unknown_order(world)
    await age(world, order)
    world.sd_server.script = ["validation"]
    assert await run(world, order) == "failed"
    assert (await load(world, order.code_1c)).state == "failed"


async def test_reconcile_skips_non_pending_and_locked(world: World) -> None:
    o = await world.draft()
    row = await load(world, o["code"])
    assert await run(world, row) == "skipped"  # draft
    order = await unknown_order(world)
    await age(world, order)
    await world.redis.set(f"order:lock:{order.id}", "x")
    assert await run(world, order) == "skipped"
    assert (await load(world, order.code_1c)).state == "outcome_unknown"


# ------------------------------------------------------------------ stuck submitting


async def test_startup_recovery_moves_stuck_submitting(world: World, session: AsyncSession) -> None:
    stuck = await world.draft()
    fresh = (await world.shop.post("/api/shop/orders", {})).json()
    for public_id, minutes in ((stuck["public_id"], 10), (fresh["public_id"], 1)):
        await session.execute(
            update(ShopOrder)
            .where(ShopOrder.public_id == public_id)
            .values(state="submitting", submitted_at=clock.utcnow() - timedelta(minutes=minutes))
        )
    await session.commit()
    ids = await order_jobs.startup_recovery(world.sync, world.jobs)
    a = await load(world, stuck["code"])
    b = await load(world, fresh["code"])
    assert ids == [a.id] and a.state == "outcome_unknown" and b.state == "submitting"
    assert (RECONCILE_ORDER, (a.id,), None) in world.jobs.enqueued
    assert (await events(world, a))[-1] == "outcome_unknown"
    await age(world, a)
    assert await run(world, a) == "sent"  # resent idempotently


async def test_reconcile_handles_submitting_state(world: World, session: AsyncSession) -> None:
    o = await world.draft()
    await session.execute(
        update(ShopOrder)
        .where(ShopOrder.public_id == o["public_id"])
        .values(state="submitting", submitted_at=clock.utcnow() - timedelta(minutes=9))
    )
    await session.commit()
    row = await load(world, o["code"])
    assert await run(world, row) == "sent"


# ------------------------------------------------------------------------ sweep


async def test_sweep_requeues_overdue_unknown_orders(world: World, session: AsyncSession) -> None:
    order = await unknown_order(world)
    assert await order_jobs.sweep_unknown(world.sync, world.jobs) == 0  # not overdue yet
    past = (clock.utcnow() - timedelta(minutes=30)).isoformat()
    await session.execute(
        update(ShopOrder)
        .where(ShopOrder.id == order.id)
        .values(last_error={"code": "outcome_unknown", "next_attempt_at": past})
    )
    await session.commit()
    assert await order_jobs.sweep_unknown(world.sync, world.jobs) == 1
    assert (RECONCILE_ORDER, (order.id,), None) in world.jobs.enqueued


# ------------------------------------------------------------------- status sync


async def sent_order(w: World, ref: str = "pb") -> ShopOrder:
    o = await w.draft([(ref, 0, 1)])
    assert (await w.submit(o)).status_code == 200
    return await load(w, o["code"])


async def test_status_sync_updates_status_and_writes_event(world: World) -> None:
    order = await sent_order(world)
    world.sd_server.orders[order.code_1c]["status"] = 2
    stats = await order_jobs.sync_order_statuses(world.sync)
    assert stats["updated"] == 1
    row = await load(world, order.code_1c)
    assert row.sd_status == 2 and row.state == "sent" and row.sd_synced_at is not None
    ev = (
        await world.session.scalars(
            select(ShopOrderEvent).where(ShopOrderEvent.event == "status_changed")
        )
    ).one()
    assert ev.actor_type == "system" and ev.payload == {"from": 1, "to": 2}
    assert (await order_jobs.sync_order_statuses(world.sync))["updated"] == 0  # no change


async def test_status_sync_cancelled_in_sd_and_back(world: World) -> None:
    order = await sent_order(world)
    world.sd_server.orders[order.code_1c]["status"] = 5
    await order_jobs.sync_order_statuses(world.sync)
    assert (await load(world, order.code_1c)).state == "cancelled"
    world.sd_server.orders[order.code_1c]["status"] = 1
    await order_jobs.sync_order_statuses(world.sync)
    assert (await load(world, order.code_1c)).state == "sent"


async def test_status_sync_ignores_foreign_orders_and_requests_all_statuses(world: World) -> None:
    order = await sent_order(world)
    world.sd_server.orders["OTHER-1"] = {"SD_id": "d0_1", "status": 3, "orderProducts": []}
    world.sd_server.orders["WEB-999999"] = {"SD_id": "d0_2", "status": 3, "orderProducts": []}
    stats = await order_jobs.sync_order_statuses(world.sync)
    assert stats["seen"] == 2 and stats["updated"] == 0  # WEB-999999 has no local order
    (call,) = world.sd_server.calls_of("getOrder")
    flt = call["params"]["filter"]
    assert flt["include"] == "all" and flt["status"] == [1, 2, 3, 4, 5]
    assert "dateCreate" in flt["period"]
    assert (await load(world, order.code_1c)).sd_status == 1


async def test_status_sync_hands_unknown_orders_to_reconcile(world: World) -> None:
    order = await unknown_order(world, "timeout_applied")
    await order_jobs.sync_order_statuses(world.sync, world.jobs)
    row = await load(world, order.code_1c)
    assert row.state == "outcome_unknown"  # only reconcile may decide what landed
    assert (RECONCILE_ORDER, (order.id,), None) in world.jobs.enqueued


async def test_status_sync_skips_locked_orders(world: World) -> None:
    order = await sent_order(world)
    world.sd_server.orders[order.code_1c]["status"] = 3
    await world.redis.set(f"order:lock:{order.id}", "x")
    stats = await order_jobs.sync_order_statuses(world.sync)
    assert stats["skipped_locked"] == 1 and (await load(world, order.code_1c)).sd_status == 1


async def test_worker_job_swallows_sd_errors(world: World) -> None:
    world.sd_server.orders_down = True
    await worker.sync_order_statuses({"sync": world.sync})  # must not raise


# ------------------------------------------------------------------- stock refresh


async def test_refresh_warehouse_stock_updates_mirror(world: World, session: AsyncSession) -> None:
    world.sd_server.stock[WH]["pb"] = 1
    del world.sd_server.stock[WH]["pa"]  # out of stock in SD -> zero in the mirror
    await order_jobs.refresh_warehouse_stock(world.sync, WH)
    await session.commit()
    q = dict(
        (
            await session.execute(
                select(SdStock.product_id, SdStock.quantity).where(
                    SdStock.product_id.in_([world.catalog["pb"].id, world.catalog["pa"].id])
                )
            )
        ).all()
    )
    assert q[world.catalog["pb"].id] == 1 and q[world.catalog["pa"].id] == 0


async def test_refresh_warehouse_stock_sd_down_is_quiet(world: World) -> None:
    world.sd_server.stock_down = True
    assert await order_jobs.refresh_warehouse_stock(world.sync, WH) == 0


# ------------------------------------------------------------------------ wiring


def test_worker_registers_order_jobs() -> None:
    names = {getattr(f, "__name__", "") for f in worker.WorkerSettings.functions}
    assert {"reconcile_order", "refresh_warehouse_stock", "sync_order_statuses"} <= names
    assert (
        RECONCILE_ORDER == "reconcile_order"
        and REFRESH_WAREHOUSE_STOCK == "refresh_warehouse_stock"
    )
    cron_names = {c.name for c in worker.WorkerSettings.cron_jobs}
    assert any("sync_order_statuses" in n for n in cron_names)
    assert any("sweep_unknown_orders" in n for n in cron_names)


async def test_worker_reconcile_wrapper_uses_ctx(world: World) -> None:
    order = await unknown_order(world)
    await age(world, order)
    ctx: dict[str, Any] = {"sync": world.sync, "jobs": world.jobs}
    assert await worker.reconcile_order(ctx, order.id) == "sent"

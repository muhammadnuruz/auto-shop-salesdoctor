"""Background order jobs: reconcile, status sync from SD, stock refresh, sweeps.

Each function takes a ``SyncContext`` (sessionmaker + SD client + Redis) plus a ``JobQueue`` where
it needs to schedule follow-ups, so the arq wrappers in ``app.workers.settings`` stay trivial.
"""

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.logging import get_logger
from app.integrations.salesdoctor import SDError
from app.models import ShopOrder
from app.services.jobs import RECONCILE_ORDER, JobQueue
from app.services.order_flow import (
    SYSTEM,
    ReconcileOutcome,
    Services,
    apply_sd_state,
    due_unknown_ids,
    reconcile_order,
    recover_stuck_submitting,
    try_lock,
)
from app.services.orders import add_event
from app.services.stock_fresh import apply_to_mirror, fetch_warehouse_stock
from app.sync.common import SD_TZ, chunks
from app.sync.context import SyncContext

log = get_logger(__name__)

STATUS_SYNC_DAYS = 30  # frequent sync window
OLD_OPEN_SYNC_DAYS = 180  # hourly sync of still-open (status 1/2) orders older than the above
SWEEP_GRACE = timedelta(minutes=5)
CODE_PREFIX = "WEB-"
SYNCED_STATES = ("sent", "cancelled")
SD_DT = "%Y-%m-%d %H:%M:%S"


async def reconcile_job(sync: SyncContext, jobs: JobQueue, order_id: int) -> ReconcileOutcome:
    async with sync.sessionmaker() as db:
        return await reconcile_order(db, Services(sync.sd, sync.redis, jobs), order_id)


async def startup_recovery(sync: SyncContext, jobs: JobQueue) -> list[int]:
    """submitting > 5 min (worker/API died mid-send) -> outcome_unknown -> reconcile."""
    async with sync.sessionmaker() as db:
        return await recover_stuck_submitting(db, jobs, sync.redis)


async def sweep_unknown(sync: SyncContext, jobs: JobQueue) -> int:
    """Per-minute: recover stuck ``submitting`` orders and re-enqueue lost reconcile jobs."""
    await startup_recovery(sync, jobs)
    async with sync.sessionmaker() as db:
        ids = await due_unknown_ids(db, grace=SWEEP_GRACE)
    for order_id in ids:
        await jobs.enqueue(RECONCILE_ORDER, order_id)
    return len(ids)


async def refresh_warehouse_stock(sync: SyncContext, warehouse_sd_id: str) -> int:
    """Pull fresh availability of one warehouse into the mirror (after a send/cancel)."""
    try:
        live = await fetch_warehouse_stock(sync.sd, warehouse_sd_id)
    except SDError as exc:
        log.warning("stock_refresh_failed", warehouse=warehouse_sd_id, error=exc.message)
        return 0
    if live is None:
        return 0
    async with sync.sessionmaker() as db:
        rows = await apply_to_mirror(db, warehouse_sd_id, live, zero_missing=True)
        await db.commit()
    return rows


def _fmt(moment: datetime) -> str:
    return moment.astimezone(SD_TZ).strftime(SD_DT)


async def _match_and_apply(
    sync: SyncContext,
    jobs: JobQueue | None,
    *,
    date_from: str,
    date_to: str,
    statuses: tuple[int, ...],
    extra_where: list[ColumnElement[bool]],
) -> dict[str, int]:
    """Snapshot SD orders in a window and apply them to our ``WEB-*`` orders.

    The snapshot can be minutes old (paginated), so rows are only applied when nothing local
    touched them since the snapshot started: skipped when updated/synced after ``started``,
    when the per-order lock is held, or when the row is locked (SKIP LOCKED).
    """
    started = clock.utcnow()
    found: dict[str, tuple[str | None, int | None]] = {}
    async for o in sync.sd.iterate_orders(statuses=statuses, date_from=date_from, date_to=date_to):
        if o.code_1C and o.code_1C.startswith(CODE_PREFIX):
            found[o.code_1C] = (o.SD_id, o.status)
    stats = {"seen": len(found), "updated": 0, "skipped_locked": 0, "skipped_fresh": 0}
    unknown_ids: list[int] = []
    for batch in chunks(list(found), 500):
        async with sync.sessionmaker() as db:
            orders = await _lock_rows(db, list(batch), extra_where)
            for order in orders:
                lock = await try_lock(sync.redis, order.id)
                if lock is None:
                    stats["skipped_locked"] += 1
                    continue
                try:
                    touched = max(order.updated_at, order.sd_synced_at or order.updated_at)
                    if touched > started:
                        stats["skipped_fresh"] += 1
                        continue
                    sd_id, status = found[order.code_1c]
                    previous = order.sd_status
                    if apply_sd_state(order, sd_id, status):
                        stats["updated"] += 1
                        if status != previous:
                            add_event(
                                db,
                                order,
                                "status_changed",
                                SYSTEM,
                                {"from": previous, "to": status},
                            )
                finally:
                    await lock.release()
            await db.commit()
            unknown_ids += await _unknown_ids(db, list(batch))
    if jobs is not None:  # present in SD but unresolved locally: reconcile decides what landed
        for order_id in unknown_ids:
            await jobs.enqueue(RECONCILE_ORDER, order_id)
    log.info("order_status_sync", **stats)
    return stats


async def _lock_rows(
    db: AsyncSession, codes: list[str], extra_where: list[ColumnElement[bool]]
) -> list[ShopOrder]:
    stmt = (
        select(ShopOrder)
        .where(ShopOrder.code_1c.in_(codes), ShopOrder.state.in_(SYNCED_STATES), *extra_where)
        .with_for_update(skip_locked=True)
    )
    return list((await db.scalars(stmt)).all())


async def _unknown_ids(db: AsyncSession, codes: list[str]) -> list[int]:
    stmt = select(ShopOrder.id).where(
        ShopOrder.code_1c.in_(codes), ShopOrder.state == "outcome_unknown"
    )
    return list((await db.scalars(stmt)).all())


async def sync_order_statuses(sync: SyncContext, jobs: JobQueue | None = None) -> dict[str, int]:
    """Every 5 min: match SD orders (last 30 days, every status) to ours by code_1C ``WEB-*``."""
    now = clock.utcnow()
    return await _match_and_apply(
        sync,
        jobs,
        date_from=_fmt(
            (now - timedelta(days=STATUS_SYNC_DAYS)).replace(hour=0, minute=0, second=0)
        ),
        date_to=_fmt((now + timedelta(days=1)).replace(hour=23, minute=59, second=59)),
        statuses=(1, 2, 3, 4, 5),
        extra_where=[],
    )


async def sync_old_open_orders(sync: SyncContext, jobs: JobQueue | None = None) -> dict[str, int]:
    """Hourly: orders older than the 30-day window that are still open in SD (status 1/2),
    looked up within a bounded 180-day window so they do not stay stale forever."""
    now = clock.utcnow()
    newest = now - timedelta(days=STATUS_SYNC_DAYS)
    oldest = now - timedelta(days=OLD_OPEN_SYNC_DAYS)
    return await _match_and_apply(
        sync,
        jobs,
        date_from=_fmt(oldest.replace(hour=0, minute=0, second=0)),
        date_to=_fmt(newest.replace(hour=23, minute=59, second=59)),
        statuses=(1, 2),
        extra_where=[
            ShopOrder.sd_status.in_((1, 2)),
            ShopOrder.created_at < newest,
            ShopOrder.created_at >= oldest,
        ],
    )

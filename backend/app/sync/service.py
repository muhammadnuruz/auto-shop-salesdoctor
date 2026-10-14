"""Sync runner: per-entity Redis lock, sync_run bookkeeping, error capture."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import distinct_on

from app.core.logging import get_logger
from app.models import SyncRun
from app.sync.balances import sync_balances
from app.sync.clients import sync_clients
from app.sync.common import SyncOutcome, utcnow
from app.sync.context import SyncContext
from app.sync.prices import sync_prices
from app.sync.products import sync_products
from app.sync.references import sync_references
from app.sync.stock import sync_stock

log = get_logger(__name__)

SyncFn = Callable[[SyncContext], Awaitable[SyncOutcome]]

ENTITIES: dict[str, SyncFn] = {
    "references": sync_references,
    "products": sync_products,
    "prices": sync_prices,
    "stock": sync_stock,
    "clients": sync_clients,
    "balances": sync_balances,
}
# Dependency order for the initial full sync (products need references, stock needs products...).
INITIAL_ORDER = ["references", "products", "prices", "stock", "clients", "balances"]
LOCK_TTL = 900.0


@dataclass(frozen=True)
class SyncResult:
    entity: str
    status: str  # ok | error | skipped
    items: int = 0
    warnings: int = 0
    error: str | None = None
    duration_ms: int = 0
    run_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "status": self.status,
            "items": self.items,
            "warnings": self.warnings,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "run_id": self.run_id,
        }


async def run_sync(ctx: SyncContext, entity: str) -> SyncResult:
    """Run one entity sync (also the hook for a future admin "sync now" button).

    Never raises for sync failures: they are recorded in ``sync_run`` and returned. Returns
    ``skipped`` when another run of the same entity holds the lock.
    """
    fn = ENTITIES.get(entity)
    if fn is None:
        raise KeyError(f"unknown sync entity: {entity}")

    lock = ctx.redis.lock(f"sync:lock:{entity}", timeout=LOCK_TTL)
    try:
        acquired = await lock.acquire(blocking=False)
    except RedisError as exc:
        return SyncResult(entity, "error", error=f"lock unavailable: {type(exc).__name__}")
    if not acquired:
        log.info("sync_skipped_locked", entity=entity)
        return SyncResult(entity, "skipped")

    started = utcnow()
    t0 = time.perf_counter()
    try:
        async with ctx.sessionmaker() as session:  # short bookkeeping transaction
            run = SyncRun(entity=entity, started_at=started, status="running")
            session.add(run)
            await session.commit()
            run_id = run.id

        outcome: SyncOutcome | None = None
        error: str | None = None
        try:
            outcome = await fn(ctx)  # fetches from SD first, then one short write transaction
        except Exception as exc:  # recorded, not raised: cron must not blow up the worker
            error = f"{type(exc).__name__}: {str(exc)[:1000]}"
            log.error("sync_failed", entity=entity, error=error)

        duration = int((time.perf_counter() - t0) * 1000)
        async with ctx.sessionmaker() as session:
            await session.execute(
                update(SyncRun)
                .where(SyncRun.id == run_id)
                .values(
                    finished_at=utcnow(),
                    status="error" if error else "ok",
                    items=outcome.items if outcome else 0,
                    warnings=outcome.warnings if outcome else 0,
                    detail=outcome.detail if outcome else None,
                    error=error,
                    duration_ms=duration,
                )
            )
            await session.commit()
        log.info("sync_done", entity=entity, status="error" if error else "ok", ms=duration)
        return SyncResult(
            entity,
            "error" if error else "ok",
            items=outcome.items if outcome else 0,
            warnings=outcome.warnings if outcome else 0,
            error=error,
            duration_ms=duration,
            run_id=run_id,
        )
    finally:
        try:
            await lock.release()
        except RedisError as exc:
            log.warning("sync_lock_release_failed", entity=entity, error=type(exc).__name__)


async def run_initial_sync(ctx: SyncContext) -> list[SyncResult]:
    """Full sync in dependency order (worker startup)."""
    return [await run_sync(ctx, e) for e in INITIAL_ORDER]


async def mark_stale_runs(ctx: SyncContext) -> int:
    """Worker startup: 'running' rows older than the lock TTL belong to a dead worker."""
    cutoff = utcnow() - timedelta(seconds=LOCK_TTL)
    async with ctx.sessionmaker() as session:
        res = await session.execute(
            update(SyncRun)
            .where(SyncRun.status == "running", SyncRun.started_at < cutoff)
            .values(status="error", error="stale: worker restarted", finished_at=utcnow())
        )
        await session.commit()
        return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def purge_sync_runs(ctx: SyncContext, days: int | None = None) -> int:
    """Retention: delete sync_run rows older than SYNC_RUN_RETENTION_DAYS."""
    keep = days if days is not None else ctx.settings.SYNC_RUN_RETENTION_DAYS
    cutoff = utcnow() - timedelta(days=keep)
    async with ctx.sessionmaker() as session:
        res = await session.execute(
            delete(SyncRun).where(SyncRun.started_at < cutoff, SyncRun.status != "running")
        )
        await session.commit()
        return int(res.rowcount or 0)  # type: ignore[attr-defined]


def _run_dict(r: SyncRun) -> dict[str, Any]:
    return {
        "id": r.id,
        "entity": r.entity,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "items": r.items,
        "warnings": r.warnings,
        "duration_ms": r.duration_ms,
        "error": r.error,
    }


async def latest_runs(ctx: SyncContext, limit: int = 20) -> dict[str, Any]:
    """Latest run per entity (DISTINCT ON, served by the (entity, started_at DESC) index) plus
    the most recent ``limit`` runs overall."""
    async with ctx.sessionmaker() as session:
        per_entity = (
            await session.scalars(
                select(SyncRun)
                .ext(distinct_on(SyncRun.entity))
                .order_by(SyncRun.entity, SyncRun.started_at.desc())
            )
        ).all()
        recent = (
            await session.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit))
        ).all()
        return {
            "latest": {r.entity: _run_dict(r) for r in per_entity},
            "recent": [_run_dict(r) for r in recent],
        }

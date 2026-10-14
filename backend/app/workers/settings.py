"""arq worker: SD sync cron jobs plus a heartbeat."""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import httpx
from arq.connections import RedisSettings
from arq.cron import CronJob, cron

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import create_engine, create_sessionmaker
from app.integrations.salesdoctor import SalesDoctorClient, TokenStore
from app.services import app_settings, notifier, order_jobs
from app.services.jobs import ArqJobQueue, JobQueue
from app.services.retention import purge_auth_data
from app.sync.context import SyncContext
from app.sync.service import mark_stale_runs, purge_sync_runs, run_initial_sync, run_sync

settings = get_settings()
log = get_logger(__name__)

JobFn = Callable[[dict[str, Any]], Awaitable[None]]


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Liveness log line (the arq health key is what the container healthcheck reads)."""
    log.info("worker_heartbeat")


def _sync_job(entity: str) -> JobFn:
    async def job(ctx: dict[str, Any]) -> None:
        await run_sync(ctx["sync"], entity)  # failures are recorded in sync_run

    job.__name__ = f"sync_{entity}"
    job.__qualname__ = job.__name__
    return job


async def purge_runs_job(ctx: dict[str, Any]) -> None:
    deleted = await purge_sync_runs(ctx["sync"])
    log.info("sync_run_purged", deleted=deleted)


async def purge_auth_job(ctx: dict[str, Any]) -> None:
    sync: SyncContext = ctx["sync"]
    async with sync.sessionmaker() as db:
        deleted = await purge_auth_data(db, sync.settings)
    log.info("auth_data_purged", **deleted)


def _jobs(ctx: dict[str, Any]) -> JobQueue:
    queue: JobQueue = ctx["jobs"]
    return queue


async def reconcile_order(ctx: dict[str, Any], order_id: int) -> str:
    """Verify/resend an order whose SD outcome is unknown (idempotent via code_1C)."""
    return await order_jobs.reconcile_job(ctx["sync"], _jobs(ctx), order_id)


async def refresh_warehouse_stock(ctx: dict[str, Any], warehouse_sd_id: str) -> int:
    return await order_jobs.refresh_warehouse_stock(ctx["sync"], warehouse_sd_id)


async def sync_order_statuses(ctx: dict[str, Any]) -> None:
    try:
        await order_jobs.sync_order_statuses(ctx["sync"], _jobs(ctx))
    except Exception as exc:  # SD down etc.: the next run catches up
        log.warning("order_status_sync_failed", error=type(exc).__name__)


async def sync_old_open_orders(ctx: dict[str, Any]) -> None:
    """Hourly: still-open orders older than the 30-day status-sync window (bounded to 180 days)."""
    try:
        await order_jobs.sync_old_open_orders(ctx["sync"], _jobs(ctx))
    except Exception as exc:
        log.warning("old_open_order_sync_failed", error=type(exc).__name__)


async def sweep_unknown_orders(ctx: dict[str, Any]) -> None:
    await order_jobs.sweep_unknown(ctx["sync"], _jobs(ctx))


SYNC_JOBS: dict[str, Any] = {e: _sync_job(e) for e in app_settings.SYNC_ENTITIES}
DISPATCH_EVERY = 10  # seconds; granularity of the DB-configurable sync intervals


async def dispatch_syncs(ctx: dict[str, Any]) -> list[str]:
    """Enqueue each entity's sync when its interval slot starts.

    Intervals come from ``app_setting`` (admin panel) with env defaults. The arq job id embeds
    the slot, so several workers (or a slow tick) never enqueue the same slot twice; a sync
    that is still running makes the next one return ``skipped`` (per-entity Redis lock).
    """
    sync: SyncContext = ctx["sync"]
    async with sync.sessionmaker() as db:
        rt = await app_settings.load(db, sync.settings)
    now = int(time.time())
    queued: list[str] = []
    for entity, interval in rt.sync_intervals.items():
        slot = now // interval
        if now - slot * interval >= DISPATCH_EVERY:
            continue  # not at the start of a slot
        job = await ctx["redis"].enqueue_job(
            f"sync_{entity}", _job_id=f"sync:{entity}:{interval}:{slot}"
        )
        if job is not None:
            queued.append(entity)
    return queued


async def notify_tick(ctx: dict[str, Any]) -> None:
    sync: SyncContext = ctx["sync"]
    lock = ctx["redis"].lock("notify:lock", timeout=120)
    if not await lock.acquire(blocking=False):
        return
    try:
        async with sync.sessionmaker() as db:
            await notifier.tick(db, ctx["http"], sync.settings)
    except Exception as exc:  # never kill the worker; next tick retries
        log.warning("notify_tick_failed", error=type(exc).__name__)
    finally:
        with contextlib.suppress(Exception):
            await lock.release()


def _cron_jobs() -> list[CronJob]:
    jobs = [
        cron(heartbeat, minute=set(range(0, 60, 5)), second=0, microsecond=0),
        cron(purge_runs_job, hour={3}, minute={15}, second=0, microsecond=0),
        cron(purge_auth_job, hour={3}, minute={30}, second=0, microsecond=0),
        cron(sweep_unknown_orders, second=30, microsecond=0),  # every minute
    ]
    if settings.SYNC_ENABLED:
        jobs.append(
            cron(sync_order_statuses, minute=set(range(0, 60, 5)), second=15, microsecond=0)
        )
        jobs.append(cron(sync_old_open_orders, minute={20}, second=45, microsecond=0))
        jobs.append(cron(dispatch_syncs, second=set(range(0, 60, DISPATCH_EVERY)), microsecond=0))
    jobs.append(cron(notify_tick, second={5, 35}, microsecond=0))
    return jobs


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(settings.LOG_LEVEL)
    http = httpx.AsyncClient(timeout=settings.SD_TIMEOUT)
    engine = create_engine(
        settings.DATABASE_URL,
        server_settings={
            "lock_timeout": "5000",
            "idle_in_transaction_session_timeout": "60000",
        },
    )
    sd = SalesDoctorClient(
        http,
        TokenStore.for_account(
            ctx["redis"],
            base_url=settings.SD_BASE_URL,
            login=settings.SD_LOGIN,
            lock_timeout=settings.SD_LOCK_TIMEOUT,
        ),
        base_url=settings.SD_BASE_URL,
        login=settings.SD_LOGIN,
        password=settings.SD_PASSWORD,
        user_agent=settings.SD_USER_AGENT,
        filial_id=settings.SD_FILIAL_ID,
        timeout=settings.SD_TIMEOUT,
        login_timeout=settings.SD_LOGIN_TIMEOUT,
        max_retries=settings.SD_MAX_RETRIES,
    )
    ctx["http"] = http
    ctx["engine"] = engine
    ctx["sync"] = SyncContext(create_sessionmaker(engine), sd, ctx["redis"], settings)
    ctx["jobs"] = ArqJobQueue(pool=ctx["redis"])
    await mark_stale_runs(ctx["sync"])
    await order_jobs.startup_recovery(ctx["sync"], ctx["jobs"])
    if settings.SYNC_ENABLED:
        # Dependency-ordered full sync in the background; cron jobs are lock-protected.
        ctx["initial_sync"] = asyncio.create_task(run_initial_sync(ctx["sync"]))


async def shutdown(ctx: dict[str, Any]) -> None:
    task = ctx.get("initial_sync")
    if task is not None and not task.done():
        task.cancel()
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        heartbeat,
        purge_runs_job,
        purge_auth_job,
        reconcile_order,
        refresh_warehouse_stock,
        sync_order_statuses,
        sync_old_open_orders,
        sweep_unknown_orders,
        dispatch_syncs,
        notify_tick,
        *SYNC_JOBS.values(),
    ]
    cron_jobs: ClassVar[list[CronJob]] = _cron_jobs()
    health_check_interval = 30
    job_timeout = 900
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)

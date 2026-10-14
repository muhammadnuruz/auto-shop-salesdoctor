"""Background-job enqueueing (arq). Enqueue failures are logged, never raised to callers."""

from typing import Any, Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.logging import get_logger

log = get_logger(__name__)

# job names registered in app.workers.settings
RECONCILE_ORDER = "reconcile_order"
REFRESH_WAREHOUSE_STOCK = "refresh_warehouse_stock"


class JobQueue(Protocol):
    async def enqueue(
        self, name: str, *args: Any, defer_by: float | None = None, job_id: str | None = None
    ) -> bool: ...


class NullJobQueue:
    """Used when no queue is configured (e.g. tests that do not care about background jobs)."""

    async def enqueue(
        self, name: str, *args: Any, defer_by: float | None = None, job_id: str | None = None
    ) -> bool:
        return False


class ArqJobQueue:
    """Enqueues onto an existing arq pool (worker) or one created lazily from a Redis URL."""

    def __init__(self, redis_url: str | None = None, pool: ArqRedis | None = None) -> None:
        self._url = redis_url
        self._pool = pool

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            if self._url is None:
                raise RuntimeError("no Redis URL configured for the job queue")
            self._pool = await create_pool(RedisSettings.from_dsn(self._url))
        return self._pool

    async def enqueue(
        self, name: str, *args: Any, defer_by: float | None = None, job_id: str | None = None
    ) -> bool:
        try:
            pool = await self._get_pool()
            job = await pool.enqueue_job(name, *args, _defer_by=defer_by, _job_id=job_id)
        except Exception as exc:  # a lost enqueue must not fail the request; sweeps recover
            log.warning("job_enqueue_failed", job=name, error=type(exc).__name__)
            return False
        return job is not None

    async def close(self) -> None:
        if self._pool is not None and self._url is not None:
            await self._pool.aclose()
            self._pool = None

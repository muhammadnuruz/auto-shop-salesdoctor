"""Redis fixed-window rate limiting."""

from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass(frozen=True)
class Limit:
    key: str
    limit: int
    window: int  # seconds


async def hit(redis: Redis, limits: list[Limit]) -> int | None:
    """Count one hit against every limit. Returns Retry-After seconds if any is exceeded."""
    retry: int | None = None
    for lim in limits:
        key = f"rl:{lim.key}:{lim.window}"
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()
        if ttl < 0:  # first hit in the window (or key lost its TTL): (re)start the window
            await redis.expire(key, lim.window)
            ttl = lim.window
        if count > lim.limit:
            retry = max(retry or 0, max(1, int(ttl)))
    return retry


async def incr_window(redis: Redis, key: str, window: int) -> tuple[int, int]:
    """Increment a fixed-window counter. Returns ``(count, seconds_left)``."""
    k = f"rl:{key}:{window}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(k)
        pipe.ttl(k)
        count, ttl = await pipe.execute()
    if ttl < 0:
        await redis.expire(k, window)
        ttl = window
    return int(count), int(ttl)

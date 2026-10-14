"""Redis-backed storage for the Sales Doctor token and the distributed login lock.

Every SD ``login`` invalidates the previous token, so exactly one process may log in at a time.
Keys are namespaced per (base_url, login) so different accounts/servers never share a token.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from redis.asyncio import Redis
from redis.asyncio.lock import Lock
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.integrations.salesdoctor.errors import SDTransportError

log = get_logger(__name__)

AUTH_KEY = "sd:auth"
LOCK_KEY = "sd:auth:lock"


class LoginLockTimeout(Exception):
    """Could not acquire the login lock within the blocking timeout."""


@dataclass(frozen=True, slots=True)
class SDCredentials:
    user_id: str
    token: str = field(repr=False)

    def __repr__(self) -> str:
        return f"SDCredentials(user_id={self.user_id!r}, token='***')"


@asynccontextmanager
async def _guard() -> AsyncIterator[None]:
    try:
        yield
    except RedisError as exc:
        raise SDTransportError(
            f"token store unavailable ({type(exc).__name__})", method="login", retryable=True
        ) from None


class TokenStore:
    def __init__(
        self,
        redis: Redis,
        *,
        key: str = AUTH_KEY,
        lock_key: str = LOCK_KEY,
        lock_timeout: float = 30.0,
    ) -> None:
        self._redis = redis
        self._key = key
        self._lock_key = lock_key
        self._lock_timeout = lock_timeout

    @classmethod
    def for_account(
        cls, redis: Redis, *, base_url: str, login: str, lock_timeout: float = 30.0
    ) -> TokenStore:
        digest = hashlib.sha256(f"{base_url}|{login}".encode()).hexdigest()[:12]
        return cls(
            redis,
            key=f"{AUTH_KEY}:{digest}",
            lock_key=f"{AUTH_KEY}:{digest}:lock",
            lock_timeout=lock_timeout,
        )

    @property
    def key(self) -> str:
        return self._key

    @property
    def lock_key(self) -> str:
        return self._lock_key

    async def get(self) -> SDCredentials | None:
        async with _guard():
            raw = await self._redis.get(self._key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return SDCredentials(user_id=str(data["userId"]), token=str(data["token"]))
        except (ValueError, KeyError, TypeError):
            return None

    async def set(self, creds: SDCredentials) -> None:
        payload = json.dumps({"userId": creds.user_id, "token": creds.token})
        async with _guard():
            await self._redis.set(self._key, payload)

    async def clear(self) -> None:
        async with _guard():
            await self._redis.delete(self._key)

    async def acquire_lock(self) -> Lock:
        """Acquire the distributed login lock (auto-expires if the holder dies)."""
        lock = self._redis.lock(
            self._lock_key, timeout=self._lock_timeout, blocking_timeout=self._lock_timeout
        )
        async with _guard():
            acquired = await lock.acquire()
        if not acquired:
            raise LoginLockTimeout
        return lock

    @staticmethod
    async def owns(lock: Lock) -> bool:
        """Whether we still hold the lock (it may have expired during a slow login)."""
        try:
            return bool(await lock.owned())
        except RedisError:
            return False

    @staticmethod
    async def release(lock: Lock) -> None:
        """Release without ever raising: an expired lock must not lose a valid login."""
        try:
            await lock.release()
        except RedisError as exc:  # includes LockError / LockNotOwnedError
            log.warning("sd_lock_release_failed", error=type(exc).__name__)

"""Admin authentication: argon2 verify, lockout, TOTP (with replay protection)."""

import asyncio
import hashlib

import pyotp
from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.net import rate_buckets
from app.core.security import (
    DUMMY_HASH,
    decrypt_secret,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models import AdminUser
from app.services import audit
from app.services.audit import RequestMeta

log = get_logger(__name__)

BAD_CREDENTIALS = "Login yoki parol noto'g'ri, yoki akkaunt vaqtincha bloklangan"
TOTP_REQUIRED = "totp_required"
TOTP_ISSUER = "Auto-Shop"
TOTP_REPLAY_TTL = 90  # > 3 * 30s steps covered by valid_window=1


async def check_totp(redis: Redis, settings: Settings, user: AdminUser, code: str | None) -> bool:
    """Verify a TOTP code (valid_window=1) and reject re-use of the same code."""
    if not code or not code.isdigit() or len(code) != 6 or not user.totp_secret:
        return False
    secret = decrypt_secret(settings, user.totp_secret)
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return False
    try:
        first_use = await redis.set(f"totp:used:{user.id}:{code}", "1", nx=True, ex=TOTP_REPLAY_TTL)
    except RedisError as exc:
        raise HTTPException(503, "Service temporarily unavailable") from exc
    return bool(first_use)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def _keys(username: str, ip: str | None) -> tuple[str, str, str]:
    """(pair failure counter, pair lock, per-username failure counter)."""
    u, i = _hash(username), _hash(rate_buckets(ip)[0])
    return f"adminfail:{u}:{i}", f"adminlock:{u}:{i}", f"adminfail:{u}"


async def clear_lockouts(redis: Redis, username: str) -> None:
    """Forget every failure counter / lock of a username (password reset, unlock)."""
    u = _hash(username.strip().lower())
    keys = [k async for k in redis.scan_iter(f"adminfail:{u}*")]
    keys += [k async for k in redis.scan_iter(f"adminlock:{u}:*")]
    if keys:
        await redis.delete(*keys)


async def _register_failure(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    *,
    username: str,
    user: AdminUser | None,
    meta: RequestMeta,
) -> None:
    """Count a failed attempt for (username, ip). Only that pair gets locked."""
    pair, lock, per_user = _keys(username, meta.ip)
    try:
        n = await redis.incr(pair)
        await redis.expire(pair, settings.ADMIN_LOCKOUT_MINUTES * 60)
        await redis.incr(per_user)
        await redis.expire(per_user, settings.ADMIN_LOCKOUT_MINUTES * 60)
        if n >= settings.ADMIN_MAX_FAILED_LOGINS:
            await redis.set(lock, "1", ex=settings.ADMIN_LOCKOUT_MINUTES * 60)
            await redis.delete(pair)
    except RedisError as exc:
        raise HTTPException(503, "Service temporarily unavailable") from exc
    if n >= settings.ADMIN_MAX_FAILED_LOGINS:
        log.warning(
            "admin_lockout",
            user_id=user.id if user else None,
            ip=meta.ip,
            minutes=settings.ADMIN_LOCKOUT_MINUTES,
        )
        audit.record(
            db,
            actor_type="system",
            actor_id=None,
            action="admin.lockout",
            entity_type="admin_user",
            entity_id=user.id if user else None,
            after={"scope": "username+ip", "minutes": settings.ADMIN_LOCKOUT_MINUTES},
            meta=meta,
        )


async def _fail(
    db: AsyncSession,
    meta: RequestMeta,
    user: AdminUser | None,
    reason: str,
    detail: str = BAD_CREDENTIALS,
) -> HTTPException:
    audit.record(
        db,
        actor_type="system",
        actor_id=None,
        action="admin.login_failed",
        entity_type="admin_user",
        entity_id=user.id if user else None,
        after={"reason": reason},
        meta=meta,
    )
    await db.commit()
    log.info("admin_login_failed", reason=reason, user_id=user.id if user else None)
    return HTTPException(401, detail)


async def _soft_delay(redis: Redis, settings: Settings, username: str) -> None:
    """Growing delay (never a lock) once a username has many recent failures from anywhere."""
    _, _, per_user = _keys(username, None)
    try:
        failures = int(await redis.get(per_user) or 0)
    except RedisError as exc:
        raise HTTPException(503, "Service temporarily unavailable") from exc
    extra = failures - settings.ADMIN_USER_DELAY_THRESHOLD
    if extra >= 0 and settings.ADMIN_USER_DELAY_BASE > 0:
        delay = min(
            settings.ADMIN_USER_DELAY_MAX, settings.ADMIN_USER_DELAY_BASE * 2 ** min(extra, 10)
        )
        await asyncio.sleep(delay)


async def authenticate(
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    *,
    username: str,
    password: str,
    totp: str | None,
    meta: RequestMeta,
) -> AdminUser:
    uname = username.strip().lower()
    user = await db.scalar(select(AdminUser).where(AdminUser.username == uname))
    now = clock.utcnow()
    await _soft_delay(redis, settings, uname)
    # Always run one argon2 verification (dummy hash for unknown users): same work either way.
    password_ok = verify_password(user.password_hash if user else DUMMY_HASH, password)
    _, lock, _ = _keys(uname, meta.ip)
    try:
        locked = bool(await redis.exists(lock))
    except RedisError as exc:
        raise HTTPException(503, "Service temporarily unavailable") from exc

    async def failed(
        reason: str, detail: str = BAD_CREDENTIALS, count: bool = True
    ) -> HTTPException:
        if count:
            await _register_failure(db, redis, settings, username=uname, user=user, meta=meta)
        return await _fail(db, meta, user, reason, detail)

    if user is None:
        raise await failed("unknown_user")
    if locked:
        raise await failed("locked", count=False)
    if not password_ok:
        raise await failed("bad_password")
    if not user.active:
        raise await failed("inactive", count=False)
    if user.totp_enabled:
        if not totp:
            raise await failed("totp_missing", TOTP_REQUIRED, count=False)
        if not await check_totp(redis, settings, user, totp):
            raise await failed("bad_totp")
    values: dict[str, object] = {"last_login_at": now}
    if needs_rehash(user.password_hash):
        values["password_hash"] = hash_password(password)
    await db.execute(update(AdminUser).where(AdminUser.id == user.id).values(**values))
    pair, _, _ = _keys(uname, meta.ip)
    await redis.delete(pair)
    return user

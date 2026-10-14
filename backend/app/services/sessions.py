"""Server-side sessions (shop + admin). DB stores only SHA-256(token); Redis caches lookups."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import hash_token, new_token
from app.models import AdminSession, ShopSession
from app.services.audit import RequestMeta

log = get_logger(__name__)


@dataclass(frozen=True)
class ShopSessionInfo:
    id: int
    token_hash: str
    client_id: int
    link_id: int
    phone_key: str
    expires_at: datetime


@dataclass(frozen=True)
class AdminSessionInfo:
    id: int
    token_hash: str
    admin_user_id: int
    expires_at: datetime


def _cache_key(kind: str, token_hash: str) -> str:
    return f"sess:{kind}:{token_hash}"


def shop_valid_key(session_id: int) -> str:
    return f"shopvalid:{session_id}"


async def _cache_get(redis: Redis, key: str) -> dict[str, object] | None:
    try:
        raw = await redis.get(key)
    except RedisError:
        log.warning("session_cache_unavailable")
        return None
    return None if raw is None else json.loads(raw)


async def _cache_set(redis: Redis, key: str, data: dict[str, object], ttl: int) -> None:
    try:
        await redis.set(key, json.dumps(data), ex=max(1, ttl))
    except RedisError:
        log.warning("session_cache_unavailable")


async def _cache_del(redis: Redis, *keys: str) -> None:
    if not keys:
        return
    try:
        await redis.delete(*keys)
    except RedisError:
        log.warning("session_cache_unavailable")


# ------------------------------------------------------------------------------------- shop


def _shop_info(row: ShopSession) -> ShopSessionInfo:
    return ShopSessionInfo(
        row.id, row.token_hash, row.client_id, row.link_id, row.phone_key, row.expires_at
    )


async def create_shop_session(
    db: AsyncSession,
    settings: Settings,
    *,
    client_id: int,
    link_id: int,
    phone_key: str,
    meta: RequestMeta,
) -> tuple[str, ShopSessionInfo]:
    """Add a session row (caller commits). Returns the raw token (cookie value) once."""
    token = new_token()
    now = clock.utcnow()
    row = ShopSession(
        token_hash=hash_token(token),
        client_id=client_id,
        link_id=link_id,
        phone_key=phone_key,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.SHOP_SESSION_DAYS),
        ip=meta.ip,
        user_agent=(meta.user_agent or "")[:512] or None,
    )
    db.add(row)
    await db.flush()
    return token, _shop_info(row)


async def resolve_shop_session(
    db: AsyncSession, redis: Redis, settings: Settings, token: str
) -> ShopSessionInfo | None:
    """Valid session for a cookie token, extending the 30-day sliding expiry on cache misses."""
    th = hash_token(token)
    now = clock.utcnow()
    key = _cache_key("shop", th)
    cached = await _cache_get(redis, key)
    if cached is not None:
        info = ShopSessionInfo(
            int(str(cached["id"])),
            th,
            int(str(cached["client_id"])),
            int(str(cached["link_id"])),
            str(cached["phone_key"]),
            datetime.fromisoformat(str(cached["expires_at"])),
        )
        if info.expires_at > now:
            return info
        await _cache_del(redis, key)
        return None
    row = await db.scalar(
        select(ShopSession).where(
            ShopSession.token_hash == th,
            ShopSession.revoked_at.is_(None),
            ShopSession.expires_at > now,
        )
    )
    if row is None:
        return None
    row.last_seen_at = now
    row.expires_at = now + timedelta(days=settings.SHOP_SESSION_DAYS)
    await db.commit()
    info = _shop_info(row)
    await _cache_set(
        redis,
        key,
        {
            "id": info.id,
            "client_id": info.client_id,
            "link_id": info.link_id,
            "phone_key": info.phone_key,
            "expires_at": info.expires_at.isoformat(),
        },
        settings.SESSION_CACHE_TTL,
    )
    return info


async def revoke_shop_sessions(
    db: AsyncSession,
    redis: Redis,
    *,
    session_id: int | None = None,
    client_id: int | None = None,
    link_id: int | None = None,
    phone_key: str | None = None,
) -> int:
    """Revoke matching active sessions, commit, and drop their cache entries."""
    if session_id is None and client_id is None and link_id is None and phone_key is None:
        raise ValueError("refusing to revoke every session")
    conds: list[ColumnElement[bool]] = [ShopSession.revoked_at.is_(None)]
    if session_id is not None:
        conds.append(ShopSession.id == session_id)
    if client_id is not None:
        conds.append(ShopSession.client_id == client_id)
    if link_id is not None:
        conds.append(ShopSession.link_id == link_id)
    if phone_key is not None:
        conds.append(ShopSession.phone_key == phone_key)
    rows = (
        await db.execute(
            update(ShopSession)
            .where(*conds)
            .values(revoked_at=clock.utcnow())
            .returning(ShopSession.id, ShopSession.token_hash)
        )
    ).all()
    await db.commit()
    keys = [_cache_key("shop", th) for _, th in rows] + [shop_valid_key(i) for i, _ in rows]
    await _cache_del(redis, *keys)
    return len(rows)


# ------------------------------------------------------------------------------------ admin


async def create_admin_session(
    db: AsyncSession, settings: Settings, *, admin_user_id: int, meta: RequestMeta
) -> tuple[str, AdminSessionInfo]:
    token = new_token()
    now = clock.utcnow()
    row = AdminSession(
        token_hash=hash_token(token),
        admin_user_id=admin_user_id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.ADMIN_SESSION_HOURS),
        ip=meta.ip,
        user_agent=(meta.user_agent or "")[:512] or None,
    )
    db.add(row)
    await db.flush()
    return token, AdminSessionInfo(row.id, row.token_hash, row.admin_user_id, row.expires_at)


async def resolve_admin_session(
    db: AsyncSession, redis: Redis, settings: Settings, token: str
) -> AdminSessionInfo | None:
    """Valid admin session: 12h absolute lifetime and 1h idle timeout (idle checked on misses)."""
    th = hash_token(token)
    now = clock.utcnow()
    key = _cache_key("admin", th)
    cached = await _cache_get(redis, key)
    if cached is not None:
        info = AdminSessionInfo(
            int(str(cached["id"])),
            th,
            int(str(cached["admin_user_id"])),
            datetime.fromisoformat(str(cached["expires_at"])),
        )
        if info.expires_at > now:
            return info
        await _cache_del(redis, key)
        return None
    row = await db.scalar(
        select(AdminSession).where(
            AdminSession.token_hash == th,
            AdminSession.revoked_at.is_(None),
            AdminSession.expires_at > now,
            AdminSession.last_seen_at > now - timedelta(minutes=settings.ADMIN_IDLE_MINUTES),
        )
    )
    if row is None:
        return None
    row.last_seen_at = now
    await db.commit()
    info = AdminSessionInfo(row.id, row.token_hash, row.admin_user_id, row.expires_at)
    ttl = min(settings.SESSION_CACHE_TTL, int((info.expires_at - now).total_seconds()))
    await _cache_set(
        redis,
        key,
        {
            "id": info.id,
            "admin_user_id": info.admin_user_id,
            "expires_at": info.expires_at.isoformat(),
        },
        ttl,
    )
    return info


async def revoke_admin_sessions(
    db: AsyncSession,
    redis: Redis,
    *,
    admin_user_id: int | None = None,
    session_id: int | None = None,
    except_session_id: int | None = None,
) -> int:
    if admin_user_id is None and session_id is None:
        raise ValueError("refusing to revoke every session")
    conds: list[ColumnElement[bool]] = [AdminSession.revoked_at.is_(None)]
    if admin_user_id is not None:
        conds.append(AdminSession.admin_user_id == admin_user_id)
    if session_id is not None:
        conds.append(AdminSession.id == session_id)
    if except_session_id is not None:
        conds.append(AdminSession.id != except_session_id)
    rows = (
        await db.execute(
            update(AdminSession)
            .where(*conds)
            .values(revoked_at=clock.utcnow())
            .returning(AdminSession.token_hash)
        )
    ).all()
    await db.commit()
    await _cache_del(redis, *[_cache_key("admin", th) for (th,) in rows])
    return len(rows)

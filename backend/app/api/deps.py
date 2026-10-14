"""Shared FastAPI dependencies: settings/redis/db, request meta, Origin + CSRF checks, auth."""

import contextlib
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.core.net import client_ip
from app.core.ratelimit import Limit, hit
from app.core.security import csrf_token as derive_csrf
from app.db.session import get_session
from app.domain.permissions import effective_permissions
from app.integrations.salesdoctor import SalesDoctorClient
from app.models import AccessLink, AdminUser, SdClient
from app.services import sessions
from app.services.audit import RequestMeta
from app.services.eligibility import evaluate_candidates, link_usable
from app.services.jobs import JobQueue, NullJobQueue
from app.services.order_flow import Services
from app.services.sessions import AdminSessionInfo, ShopSessionInfo

SHOP_COOKIE = "shop_session"
ADMIN_COOKIE = "admin_session"
SHOP_COOKIE_PATH = "/api/shop"
ADMIN_COOKIE_PATH = "/api/admin"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


def get_sd(request: Request) -> SalesDoctorClient:
    sd: SalesDoctorClient = request.app.state.sd
    return sd


def get_jobs(request: Request) -> JobQueue:
    jobs: JobQueue | None = getattr(request.app.state, "jobs", None)
    return jobs if jobs is not None else NullJobQueue()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RedisDep = Annotated[Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_session)]
SdDep = Annotated[SalesDoctorClient, Depends(get_sd)]
JobsDep = Annotated[JobQueue, Depends(get_jobs)]


def get_services(sd: SdDep, redis: RedisDep, jobs: JobsDep) -> Services:
    return Services(sd, redis, jobs)


ServicesDep = Annotated[Services, Depends(get_services)]


def get_meta(request: Request, settings: SettingsDep) -> RequestMeta:
    return RequestMeta(
        ip=client_ip(request, settings.trusted_proxies),
        user_agent=request.headers.get("user-agent"),
        request_id=getattr(request.state, "request_id", None),
    )


MetaDep = Annotated[RequestMeta, Depends(get_meta)]


def check_origin(request: Request, settings: SettingsDep) -> None:
    """Reject state-changing browser requests from origins outside ALLOWED_ORIGINS.

    Requests carrying neither Origin nor Referer (non-browser clients) pass here; they still
    need the session cookie + X-CSRF-Token, and cookies are SameSite=Lax.
    """
    if request.method in SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if not referer:
            return
        parts = urlsplit(referer)
        origin = f"{parts.scheme}://{parts.netloc}"
    if origin.rstrip("/") not in settings.allowed_origins:
        raise HTTPException(403, "Origin not allowed")


def check_csrf(request: Request, settings: SettingsDep, token_hash: str) -> str:
    expected = derive_csrf(settings, token_hash)
    if request.method not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(403, "CSRF token missing or invalid")
    return expected


async def enforce_rate_limit(redis: Redis, limits: list[Limit]) -> None:
    """429 + Retry-After when exceeded. Fails closed (503) if Redis is down."""
    try:
        retry = await hit(redis, limits)
    except RedisError as exc:
        raise HTTPException(503, "Service temporarily unavailable") from exc
    if retry is not None:
        raise HTTPException(
            429, "Too many attempts, try again later", headers={"Retry-After": str(retry)}
        )


def set_session_cookie(
    response: Response, settings: Settings, *, name: str, path: str, token: str, max_age: int
) -> None:
    response.set_cookie(
        name,
        token,
        max_age=max_age,
        path=path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings, *, name: str, path: str) -> None:
    response.delete_cookie(
        name, path=path, httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


# ------------------------------------------------------------------------------------- shop


@dataclass
class ShopContext:
    client: SdClient
    link: AccessLink
    session: ShopSessionInfo
    csrf_token: str


async def current_shop(
    request: Request, db: DbDep, redis: RedisDep, settings: SettingsDep
) -> ShopContext:
    token = request.cookies.get(SHOP_COOKIE)
    if not token:
        raise HTTPException(401, "Not authenticated")
    info = await sessions.resolve_shop_session(db, redis, settings, token)
    if info is None:
        raise HTTPException(401, "Not authenticated")
    csrf = check_csrf(request, settings, info.token_hash)
    client = await db.get(SdClient, info.client_id)
    link = await db.get(AccessLink, info.link_id)
    now = clock.utcnow()

    async def reject() -> HTTPException:
        await sessions.revoke_shop_sessions(db, redis, session_id=info.id)
        return HTTPException(401, "Not authenticated")

    if client is None or link is None or not client.active or not link_usable(link, now):
        raise await reject()
    vkey = sessions.shop_valid_key(info.id)
    try:
        fresh = await redis.get(vkey)
    except RedisError:
        fresh = None
    if fresh is None:  # full re-check (phone flag, client block, scope) at most every 60s
        verdict = await evaluate_candidates(db, info.phone_key, link, client_id=client.id)
        if not verdict or verdict[0][1] is not None:
            raise await reject()
        with contextlib.suppress(RedisError):
            await redis.set(vkey, "1", ex=settings.SHOP_REVALIDATE_TTL)
    return ShopContext(client, link, info, csrf)


ShopDep = Annotated[ShopContext, Depends(current_shop)]


# ------------------------------------------------------------------------------------ admin


@dataclass
class AdminContext:
    user: AdminUser
    session: AdminSessionInfo
    csrf_token: str
    permissions: frozenset[str]


async def _admin_context(
    request: Request, db: AsyncSession, redis: Redis, settings: Settings
) -> AdminContext:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        raise HTTPException(401, "Not authenticated")
    info = await sessions.resolve_admin_session(db, redis, settings, token)
    if info is None:
        raise HTTPException(401, "Not authenticated")
    csrf = check_csrf(request, settings, info.token_hash)
    user = await db.get(AdminUser, info.admin_user_id)
    if user is None or not user.active:
        await sessions.revoke_admin_sessions(db, redis, session_id=info.id)
        raise HTTPException(401, "Not authenticated")
    return AdminContext(user, info, csrf, effective_permissions(user.role, user.extra_permissions))


async def current_admin_any(
    request: Request, db: DbDep, redis: RedisDep, settings: SettingsDep
) -> AdminContext:
    """Authenticated admin, even if a password change is pending (me / password / logout)."""
    return await _admin_context(request, db, redis, settings)


async def current_admin(
    request: Request, db: DbDep, redis: RedisDep, settings: SettingsDep
) -> AdminContext:
    ctx = await _admin_context(request, db, redis, settings)
    if ctx.user.must_change_password:
        raise HTTPException(403, "password_change_required")
    return ctx


AdminAnyDep = Annotated[AdminContext, Depends(current_admin_any)]
AdminDep = Annotated[AdminContext, Depends(current_admin)]


def require(perm: str) -> Callable[[AdminContext], Awaitable[AdminContext]]:
    """Dependency factory: the admin must hold ``perm`` (superadmin holds all)."""

    async def dep(ctx: AdminDep) -> AdminContext:
        if perm not in ctx.permissions:
            raise HTTPException(403, "Forbidden")
        return ctx

    return dep


def require_any(*perms: str) -> Callable[[AdminContext], Awaitable[AdminContext]]:
    """Dependency factory: the admin must hold at least one of ``perms`` (superadmin holds all)."""
    if not perms:
        raise ValueError("require_any needs at least one permission")
    wanted = frozenset(perms)

    async def dep(ctx: AdminDep) -> AdminContext:
        if not (wanted & ctx.permissions):
            raise HTTPException(403, "Forbidden")
        return ctx

    return dep

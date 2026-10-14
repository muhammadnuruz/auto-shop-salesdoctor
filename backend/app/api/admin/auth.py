"""Admin auth endpoints: login/logout/me, password change, TOTP management."""

import hashlib
from typing import Any

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import update

from app.api.deps import (
    ADMIN_COOKIE,
    ADMIN_COOKIE_PATH,
    AdminAnyDep,
    DbDep,
    MetaDep,
    RedisDep,
    SettingsDep,
    clear_session_cookie,
    enforce_rate_limit,
    set_session_cookie,
)
from app.core.net import rate_buckets
from app.core.ratelimit import Limit
from app.core.security import (
    csrf_token,
    encrypt_secret,
    hash_password,
    password_problem,
    verify_password,
)
from app.domain.permissions import effective_permissions
from app.models import AdminUser
from app.services import admin_auth, audit, sessions
from app.services.admin_auth import BAD_CREDENTIALS, TOTP_ISSUER

router = APIRouter(prefix="/auth", tags=["admin-auth"])


class LoginIn(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=256)
    totp: str | None = Field(None, max_length=12)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class TotpSetupIn(BaseModel):
    password: str = Field(max_length=256)


class TotpEnableIn(BaseModel):
    code: str = Field(max_length=12)
    password: str = Field(max_length=256)


class TotpDisableIn(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field(max_length=12)


async def limit_password_attempts(redis: RedisDep, settings: SettingsDep, ctx: AdminAnyDep) -> None:
    """Throttle endpoints that verify the current password, per session and per user."""
    await enforce_rate_limit(
        redis,
        [
            Limit(f"adminpw:sess:{ctx.session.id}", settings.RL_ADMIN_PASSWORD_PER_MIN, 60),
            Limit(f"adminpw:user:{ctx.user.id}", settings.RL_ADMIN_PASSWORD_PER_MIN * 2, 60),
            Limit(f"adminpw:user:{ctx.user.id}", settings.RL_ADMIN_PASSWORD_PER_HOUR, 3600),
        ],
    )


PwLimit = Depends(limit_password_attempts)


def user_out(user: AdminUser, permissions: frozenset[str]) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "extra_permissions": list(user.extra_permissions),
        "permissions": sorted(permissions),
        "totp_enabled": user.totp_enabled,
        "active": user.active,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


@router.post("/login")
async def login(
    body: LoginIn,
    response: Response,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> dict[str, Any]:
    uname = body.username.strip().lower()
    uh = hashlib.sha256(uname.encode()).hexdigest()[:24]
    ip, subnet = rate_buckets(meta.ip)
    await enforce_rate_limit(
        redis,
        [
            Limit(f"admin:ip:{ip}", settings.RL_ADMIN_IP_PER_MIN, 60),
            Limit(f"admin:ip:{ip}", settings.RL_ADMIN_IP_PER_HOUR, 3600),
            Limit(f"admin:net:{subnet}", settings.RL_ADMIN_SUBNET_PER_MIN, 60),
            Limit(f"admin:net:{subnet}", settings.RL_ADMIN_SUBNET_PER_HOUR, 3600),
            Limit(f"admin:user:{uh}", settings.RL_ADMIN_USER_PER_MIN, 60),
            Limit(f"admin:user:{uh}", settings.RL_ADMIN_USER_PER_HOUR, 3600),
        ],
    )
    user = await admin_auth.authenticate(
        db,
        redis,
        settings,
        username=body.username,
        password=body.password,
        totp=body.totp,
        meta=meta,
    )
    token, info = await sessions.create_admin_session(
        db, settings, admin_user_id=user.id, meta=meta
    )
    audit.record(
        db,
        actor_type="admin",
        actor_id=user.id,
        action="admin.login",
        entity_type="admin_user",
        entity_id=user.id,
        after={"session_id": info.id},
        meta=meta,
    )
    await db.commit()
    await db.refresh(user)
    set_session_cookie(
        response,
        settings,
        name=ADMIN_COOKIE,
        path=ADMIN_COOKIE_PATH,
        token=token,
        max_age=settings.ADMIN_SESSION_HOURS * 3600,
    )
    return {
        "csrf_token": csrf_token(settings, info.token_hash),
        "must_change_password": user.must_change_password,
        "user": user_out(user, effective_permissions(user.role, user.extra_permissions)),
    }


@router.post("/logout", status_code=204)
async def logout(
    ctx: AdminAnyDep,
    response: Response,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> None:
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin.logout",
        entity_type="admin_user",
        entity_id=ctx.user.id,
        meta=meta,
    )
    await sessions.revoke_admin_sessions(db, redis, session_id=ctx.session.id)
    clear_session_cookie(response, settings, name=ADMIN_COOKIE, path=ADMIN_COOKIE_PATH)


@router.get("/me")
async def me(ctx: AdminAnyDep) -> dict[str, Any]:
    return {"user": user_out(ctx.user, ctx.permissions), "csrf_token": ctx.csrf_token}


@router.post("/password", status_code=204, dependencies=[PwLimit])
async def change_password(
    body: PasswordIn, ctx: AdminAnyDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> None:
    if not verify_password(ctx.user.password_hash, body.current_password):
        raise HTTPException(400, "Joriy parol noto'g'ri")
    problem = password_problem(body.new_password, ctx.user.username)
    if problem:
        raise HTTPException(422, problem)
    if body.new_password == body.current_password:
        raise HTTPException(422, "New password must differ from the current one")
    await db.execute(
        update(AdminUser)
        .where(AdminUser.id == ctx.user.id)
        .values(password_hash=hash_password(body.new_password), must_change_password=False)
    )
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin.password_changed",
        entity_type="admin_user",
        entity_id=ctx.user.id,
        meta=meta,
    )
    # commits; other sessions of this user are logged out, the current one stays
    await sessions.revoke_admin_sessions(
        db, redis, admin_user_id=ctx.user.id, except_session_id=ctx.session.id
    )


@router.post("/totp/setup", dependencies=[PwLimit])
async def totp_setup(
    body: TotpSetupIn, ctx: AdminAnyDep, db: DbDep, settings: SettingsDep, meta: MetaDep
) -> dict[str, str]:
    """Generate a pending TOTP secret (shown once). Not active until /totp/enable."""
    if ctx.user.totp_enabled:
        raise HTTPException(409, "TOTP already enabled")
    if not verify_password(ctx.user.password_hash, body.password):
        raise HTTPException(400, BAD_CREDENTIALS)
    secret = pyotp.random_base32()
    await db.execute(
        update(AdminUser)
        .where(AdminUser.id == ctx.user.id)
        .values(totp_secret=encrypt_secret(settings, secret))
    )
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin.totp_setup",
        entity_type="admin_user",
        entity_id=ctx.user.id,
        meta=meta,
    )
    await db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=ctx.user.username, issuer_name=TOTP_ISSUER)
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/totp/enable", status_code=204, dependencies=[PwLimit])
async def totp_enable(
    body: TotpEnableIn,
    ctx: AdminAnyDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> None:
    if ctx.user.totp_enabled:
        raise HTTPException(409, "TOTP already enabled")
    if not ctx.user.totp_secret:
        raise HTTPException(409, "Run /totp/setup first")
    if not verify_password(ctx.user.password_hash, body.password):
        raise HTTPException(400, BAD_CREDENTIALS)
    if not await admin_auth.check_totp(redis, settings, ctx.user, body.code):
        raise HTTPException(400, "Invalid code")
    await db.execute(update(AdminUser).where(AdminUser.id == ctx.user.id).values(totp_enabled=True))
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin.totp_enabled",
        entity_type="admin_user",
        entity_id=ctx.user.id,
        meta=meta,
    )
    await db.commit()


@router.post("/totp/disable", status_code=204, dependencies=[PwLimit])
async def totp_disable(
    body: TotpDisableIn,
    ctx: AdminAnyDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> None:
    if not ctx.user.totp_enabled:
        raise HTTPException(409, "TOTP is not enabled")
    if not verify_password(ctx.user.password_hash, body.password):
        raise HTTPException(400, BAD_CREDENTIALS)
    if not await admin_auth.check_totp(redis, settings, ctx.user, body.code):
        raise HTTPException(400, "Invalid code")
    await db.execute(
        update(AdminUser)
        .where(AdminUser.id == ctx.user.id)
        .values(totp_enabled=False, totp_secret=None)
    )
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin.totp_disabled",
        entity_type="admin_user",
        entity_id=ctx.user.id,
        meta=meta,
    )
    await db.commit()

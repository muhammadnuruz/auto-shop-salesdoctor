"""Admin users CRUD (admins.manage)."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.auth import user_out
from app.api.deps import AdminContext, DbDep, MetaDep, RedisDep, require
from app.core.security import generate_password, hash_password, password_problem
from app.domain.permissions import effective_permissions, unknown_permissions
from app.models import AdminUser
from app.services import admin_auth, audit, sessions

router = APIRouter(prefix="/users", tags=["admin-users"])
ManageDep = Annotated[AdminContext, Depends(require("admins.manage"))]

Role = Literal["superadmin", "admin", "operator", "viewer"]


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,64}$")
    full_name: str = Field("", max_length=200)
    role: Role
    extra_permissions: list[str] = Field(default_factory=list)
    password: str | None = Field(None, max_length=256)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=200)
    role: Role | None = None
    extra_permissions: list[str] | None = None
    active: bool | None = None


def _snapshot(u: AdminUser) -> dict[str, Any]:
    return {
        "username": u.username,
        "full_name": u.full_name,
        "role": u.role,
        "extra_permissions": list(u.extra_permissions),
        "active": u.active,
    }


def _out(u: AdminUser) -> dict[str, Any]:
    return user_out(u, effective_permissions(u.role, u.extra_permissions))


async def _get(db: AsyncSession, user_id: int, *, lock: bool = False) -> AdminUser:
    stmt = select(AdminUser).where(AdminUser.id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    user = await db.scalar(stmt)
    if user is None:
        raise HTTPException(404, "Admin user not found")
    return user


def _check_grant(ctx: AdminContext, role: str, extra: list[str]) -> None:
    """No privilege escalation: you can only hand out what you hold; only superadmins touch
    superadmin accounts."""
    bad = unknown_permissions(extra)
    if bad:
        raise HTTPException(422, f"unknown permissions: {bad}")
    if ctx.user.role == "superadmin":
        return
    if role == "superadmin" or not effective_permissions(role, extra) <= ctx.permissions:
        raise HTTPException(403, "Cannot grant permissions you do not hold")


ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2, "superadmin": 3}


def _check_target(ctx: AdminContext, target: AdminUser) -> None:
    """A delegated admins.manage holder may only act on users that are no more powerful than
    themselves: rank(target) <= rank(actor) and target's effective permissions are a subset of
    the actor's. Superadmins may act on anyone."""
    if ctx.user.role == "superadmin":
        return
    perms = effective_permissions(target.role, target.extra_permissions)
    if (
        target.role == "superadmin"
        or ROLE_RANK[target.role] > ROLE_RANK[ctx.user.role]
        or not (perms <= ctx.permissions)
    ):
        raise HTTPException(403, "Cannot manage a user with more privileges than you")


async def _ensure_other_active_superadmin(db: AsyncSession, user_id: int) -> None:
    others = (
        await db.scalars(
            select(AdminUser.id)
            .where(
                AdminUser.role == "superadmin", AdminUser.active.is_(True), AdminUser.id != user_id
            )
            .with_for_update()
        )
    ).all()
    if not others:
        raise HTTPException(409, "Cannot remove the last active superadmin")


@router.get("")
async def list_users(
    ctx: ManageDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    total = await db.scalar(select(func.count()).select_from(AdminUser))
    rows = (
        await db.scalars(select(AdminUser).order_by(AdminUser.id).limit(limit).offset(offset))
    ).all()
    return {"total": total, "items": [_out(u) for u in rows]}


@router.get("/{user_id}")
async def get_user(user_id: int, ctx: ManageDep, db: DbDep) -> dict[str, Any]:
    return _out(await _get(db, user_id))


@router.post("", status_code=201)
async def create_user(body: UserCreate, ctx: ManageDep, db: DbDep, meta: MetaDep) -> dict[str, Any]:
    _check_grant(ctx, body.role, body.extra_permissions)
    username = body.username.lower()
    temp: str | None = None
    password = body.password
    if password is None:
        password = temp = generate_password()
    else:
        problem = password_problem(password, username)
        if problem:
            raise HTTPException(422, problem)
    user = AdminUser(
        username=username,
        full_name=body.full_name,
        password_hash=hash_password(password),
        role=body.role,
        extra_permissions=sorted(set(body.extra_permissions)),
        must_change_password=True,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Username already exists") from exc
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin_user.create",
        entity_type="admin_user",
        entity_id=user.id,
        after=_snapshot(user),
        meta=meta,
    )
    await db.commit()
    await db.refresh(user)
    out = _out(user)
    if temp is not None:
        out["temporary_password"] = temp  # shown once
    return out


@router.patch("/{user_id}")
async def update_user(
    user_id: int, body: UserUpdate, ctx: ManageDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> dict[str, Any]:
    user = await _get(db, user_id, lock=True)
    before = _snapshot(user)
    _check_target(ctx, user)
    new_role = body.role if body.role is not None else user.role
    new_extra = (
        body.extra_permissions if body.extra_permissions is not None else user.extra_permissions
    )
    new_active = body.active if body.active is not None else user.active
    if user.id == ctx.user.id and not new_active:
        raise HTTPException(409, "Cannot deactivate yourself")
    if body.role is not None or body.extra_permissions is not None:
        _check_grant(ctx, new_role, list(new_extra))
    if user.role == "superadmin" and user.active and (new_role != "superadmin" or not new_active):
        await _ensure_other_active_superadmin(db, user.id)
    if body.full_name is not None:
        user.full_name = body.full_name
    user.role = new_role
    user.extra_permissions = sorted(set(new_extra))
    deactivated = user.active and not new_active
    user.active = new_active
    await db.flush()
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin_user.update",
        entity_type="admin_user",
        entity_id=user.id,
        before=before,
        after=_snapshot(user),
        meta=meta,
    )
    if deactivated or before["role"] != new_role:
        await sessions.revoke_admin_sessions(db, redis, admin_user_id=user.id)  # commits
    else:
        await db.commit()
    return _out(user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int, ctx: ManageDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> None:
    if user_id == ctx.user.id:
        raise HTTPException(409, "Cannot delete yourself")
    user = await _get(db, user_id, lock=True)
    _check_target(ctx, user)
    if user.role == "superadmin" and user.active:
        await _ensure_other_active_superadmin(db, user.id)
    before = _snapshot(user)
    await sessions.revoke_admin_sessions(db, redis, admin_user_id=user.id)
    try:
        await db.delete(user)
        audit.record(
            db,
            actor_type="admin",
            actor_id=ctx.user.id,
            action="admin_user.delete",
            entity_type="admin_user",
            entity_id=user_id,
            before=before,
            meta=meta,
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "User owns records; deactivate instead of deleting") from exc


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int, ctx: ManageDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> dict[str, str]:
    user = await _get(db, user_id, lock=True)
    _check_target(ctx, user)
    temp = generate_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    await admin_auth.clear_lockouts(redis, user.username)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin_user.reset_password",
        entity_type="admin_user",
        entity_id=user.id,
        meta=meta,
    )
    await sessions.revoke_admin_sessions(db, redis, admin_user_id=user.id)  # commits
    return {"temporary_password": temp}


@router.post("/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: int, ctx: ManageDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> dict[str, int]:
    user = await _get(db, user_id)
    _check_target(ctx, user)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin_user.revoke_sessions",
        entity_type="admin_user",
        entity_id=user.id,
        meta=meta,
    )
    return {"revoked": await sessions.revoke_admin_sessions(db, redis, admin_user_id=user.id)}

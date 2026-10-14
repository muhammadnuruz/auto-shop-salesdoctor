"""Client moderation (clients.read / clients.write): block, phone flag overrides, sessions."""

import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminContext, DbDep, MetaDep, RedisDep, SettingsDep, require
from app.core import clock
from app.domain.phone import evaluate_phone
from app.models import ClientPhone, ClientSetting, PhoneFlag, SdClient
from app.services import audit, sessions

router = APIRouter(tags=["admin-clients"])
ReadDep = Annotated[AdminContext, Depends(require("clients.read"))]
WriteDep = Annotated[AdminContext, Depends(require("clients.write"))]

PhoneKey = Annotated[str, Path(pattern=r"^[0-9]{9}$")]


class BlockIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


class PhoneOverrideIn(BaseModel):
    status: Literal["allowed", "blocked", "auto"]
    reason: str | None = Field(None, max_length=500)


async def _client(db: AsyncSession, client_id: int) -> SdClient:
    client = await db.get(SdClient, client_id)
    if client is None:
        raise HTTPException(404, "Client not found")
    return client


@router.get("/clients")
async def list_clients(
    ctx: ReadDep,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    blocked: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    conds: list[Any] = []
    if q:
        digits = re.sub(r"\D", "", q)
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        by_phone = (
            SdClient.id.in_(
                select(ClientPhone.client_id).where(ClientPhone.phone_key == digits[-9:])
            )
            if len(digits) >= 9
            else None
        )
        conds.append(
            or_(
                SdClient.name.ilike(like),
                SdClient.sd_id == q,
                *([by_phone] if by_phone is not None else []),
            )
        )
    if blocked is not None:
        blocked_ids = select(ClientSetting.client_id).where(ClientSetting.blocked.is_(True))
        conds.append(SdClient.id.in_(blocked_ids) if blocked else SdClient.id.not_in(blocked_ids))
    total = await db.scalar(select(func.count()).select_from(SdClient).where(*conds))
    rows = (
        await db.execute(
            select(SdClient, ClientSetting.blocked, ClientSetting.blocked_reason)
            .outerjoin(ClientSetting, ClientSetting.client_id == SdClient.id)
            .where(*conds)
            .order_by(SdClient.name, SdClient.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": c.id,
                "sd_id": c.sd_id,
                "name": c.name,
                "address": c.address,
                "active": c.active,
                "blocked": bool(b),
                "blocked_reason": r,
            }
            for c, b, r in rows
        ],
    }


async def _set_blocked(
    client_id: int,
    blocked: bool,
    reason: str | None,
    ctx: AdminContext,
    db: AsyncSession,
    redis: Any,
    meta: audit.RequestMeta,
) -> dict[str, Any]:
    await _client(db, client_id)
    prev = await db.get(ClientSetting, client_id)
    before = {
        "blocked": bool(prev and prev.blocked),
        "reason": prev.blocked_reason if prev else None,
    }
    values = {
        "blocked": blocked,
        "blocked_reason": reason if blocked else None,
        "updated_by": ctx.user.id,
        "updated_at": func.now(),
    }
    await db.execute(
        pg_insert(ClientSetting)
        .values(client_id=client_id, **values)
        .on_conflict_do_update(index_elements=[ClientSetting.client_id], set_=values)
    )
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="client.block" if blocked else "client.unblock",
        entity_type="sd_client",
        entity_id=client_id,
        before=before,
        after={"blocked": blocked, "reason": reason if blocked else None},
        meta=meta,
    )
    if blocked:
        revoked = await sessions.revoke_shop_sessions(db, redis, client_id=client_id)  # commits
    else:
        await db.commit()
        revoked = 0
    return {"client_id": client_id, "blocked": blocked, "revoked_sessions": revoked}


@router.post("/clients/{client_id}/block")
async def block_client(
    client_id: int, body: BlockIn, ctx: WriteDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> dict[str, Any]:
    return await _set_blocked(client_id, True, body.reason, ctx, db, redis, meta)


@router.post("/clients/{client_id}/unblock")
async def unblock_client(
    client_id: int, ctx: WriteDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> dict[str, Any]:
    return await _set_blocked(client_id, False, None, ctx, db, redis, meta)


@router.post("/clients/{client_id}/revoke-sessions")
async def revoke_client_sessions(
    client_id: int, ctx: WriteDep, db: DbDep, redis: RedisDep, meta: MetaDep
) -> dict[str, int]:
    await _client(db, client_id)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="client.revoke_sessions",
        entity_type="sd_client",
        entity_id=client_id,
        meta=meta,
    )
    return {"revoked": await sessions.revoke_shop_sessions(db, redis, client_id=client_id)}


@router.get("/phones")
async def list_phones(
    ctx: ReadDep,
    db: DbDep,
    status: Literal["ok", "suspicious", "blocked", "allowed"] | None = None,
    q: Annotated[str | None, Query(pattern=r"^[0-9]{1,9}$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    conds: list[Any] = []
    if status:
        conds.append(PhoneFlag.status == status)
    if q:
        conds.append(PhoneFlag.phone_key.startswith(q))
    total = await db.scalar(select(func.count()).select_from(PhoneFlag).where(*conds))
    rows = (
        await db.scalars(
            select(PhoneFlag)
            .where(*conds)
            .order_by(PhoneFlag.client_count.desc(), PhoneFlag.phone_key)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "total": total,
        "items": [
            {
                "phone_key": f.phone_key,
                "status": f.status,
                "reason": f.reason,
                "client_count": f.client_count,
                "updated_at": f.updated_at,
            }
            for f in rows
        ],
    }


@router.put("/phones/{phone_key}")
async def override_phone(
    phone_key: PhoneKey,
    body: PhoneOverrideIn,
    ctx: WriteDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> dict[str, Any]:
    """Set a phone to allowed/blocked, or ``auto`` to return it to automatic evaluation."""
    prev = await db.get(PhoneFlag, phone_key)
    count = await db.scalar(
        select(func.count(func.distinct(ClientPhone.client_id)))
        .join(SdClient, SdClient.id == ClientPhone.client_id)
        .where(ClientPhone.phone_key == phone_key, SdClient.active.is_(True))
    )
    count = count or 0
    if body.status == "auto":
        status, reason = evaluate_phone(phone_key, count, settings.PHONE_MAX_CLIENTS)
    else:
        status, reason = body.status, body.reason
    values = {
        "status": status,
        "reason": reason,
        "client_count": count,
        "updated_at": clock.utcnow(),
    }
    await db.execute(
        pg_insert(PhoneFlag)
        .values(phone_key=phone_key, **values)
        .on_conflict_do_update(index_elements=[PhoneFlag.phone_key], set_=values)
    )
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="phone.override",
        entity_type="phone_flag",
        entity_id=phone_key,
        before={"status": prev.status, "reason": prev.reason} if prev else None,
        after={"status": status, "reason": reason, "requested": body.status},
        meta=meta,
    )
    revoked = 0
    if status in ("blocked", "suspicious"):
        revoked = await sessions.revoke_shop_sessions(db, redis, phone_key=phone_key)  # commits
    else:
        await db.commit()
    return {"phone_key": phone_key, "status": status, "reason": reason, "revoked_sessions": revoked}

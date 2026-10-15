"""Audit log browser (audit.read): filters by actor, action, entity and period."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import ColumnElement, func, select

from app.api.deps import AdminContext, DbDep, require
from app.models import AdminUser, AuditLog

router = APIRouter(prefix="/audit", tags=["admin-audit"])
ReadDep = Annotated[AdminContext, Depends(require("audit.read"))]


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor_type: str
    actor_id: int | None
    actor_name: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    before: Any
    after: Any
    ip: str | None
    user_agent: str | None
    request_id: str | None


class AuditPage(BaseModel):
    total: int
    items: list[AuditOut]


class AuditFacets(BaseModel):
    actions: list[str]
    entity_types: list[str]
    actors: list[dict[str, Any]]  # admins that appear in the log: {id, username}


@router.get("", response_model=AuditPage)
async def list_audit(
    ctx: ReadDep,
    db: DbDep,
    actor_type: Literal["admin", "shop", "system"] | None = None,
    actor_id: Annotated[int | None, Query(ge=1)] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    entity_type: Annotated[str | None, Query(max_length=50)] = None,
    entity_id: Annotated[str | None, Query(max_length=100)] = None,
    date_from: AwareDatetime | None = None,
    date_to: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> dict[str, Any]:
    where: list[ColumnElement[bool]] = []
    if actor_type is not None:
        where.append(AuditLog.actor_type == actor_type)
    if actor_id is not None:
        where.append(AuditLog.actor_id == actor_id)
    if action:
        # "order." matches a whole family, otherwise exact
        if action.endswith("."):
            where.append(AuditLog.action.startswith(action, autoescape=True))
        else:
            where.append(AuditLog.action == action)
    if entity_type:
        where.append(AuditLog.entity_type == entity_type)
    if entity_id:
        where.append(AuditLog.entity_id == entity_id)
    if date_from is not None:
        where.append(AuditLog.at >= date_from)
    if date_to is not None:
        where.append(AuditLog.at < date_to)

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*where)) or 0
    rows = (
        await db.execute(
            select(AuditLog, AdminUser.username)
            .outerjoin(
                AdminUser,
                (AuditLog.actor_type == "admin") & (AdminUser.id == AuditLog.actor_id),
            )
            .where(*where)
            .order_by(AuditLog.at.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "at": a.at,
                "actor_type": a.actor_type,
                "actor_id": a.actor_id,
                "actor_name": username,
                "action": a.action,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "before": a.before,
                "after": a.after,
                "ip": a.ip,
                "user_agent": a.user_agent,
                "request_id": a.request_id,
            }
            for a, username in rows
        ],
    }


@router.get("/facets", response_model=AuditFacets)
async def audit_facets(ctx: ReadDep, db: DbDep) -> dict[str, Any]:
    """Values for the filter dropdowns."""
    actions = (await db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action))).all()
    entity_types = (
        await db.scalars(
            select(AuditLog.entity_type)
            .where(AuditLog.entity_type.is_not(None))
            .distinct()
            .order_by(AuditLog.entity_type)
        )
    ).all()
    actors = (
        await db.execute(select(AdminUser.id, AdminUser.username).order_by(AdminUser.username))
    ).all()
    return {
        "actions": list(actions),
        "entity_types": [e for e in entity_types if e],
        "actors": [{"id": i, "username": u} for i, u in actors],
    }

"""Access links CRUD (links.read / links.write)."""

import secrets
import string
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from sqlalchemy import ColumnElement, delete, func, literal_column, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.dashboard import PLACED
from app.api.deps import AdminContext, DbDep, MetaDep, require
from app.core import clock
from app.domain.link_settings import LinkSettings, validate_link_settings
from app.models import AccessLink, LinkClient, SdClient, ShopOrder, ShopSession
from app.services import audit
from app.services.orders import num
from app.sync.common import SD_TZ

router = APIRouter(prefix="/links", tags=["admin-links"])
ReadDep = Annotated[AdminContext, Depends(require("links.read"))]
WriteDep = Annotated[AdminContext, Depends(require("links.write"))]

SLUG_ALPHABET = string.ascii_lowercase + string.digits
SLUG_PATTERN = r"^[a-z0-9-]{6,64}$"  # custom slugs; auto slugs are 8 random chars
GUESSABLE_WORDS = frozenset(
    {"shop", "test", "dokon", "magazin", "admin", "link", "demo", "store", "order", "main", "new"}
)
Scope = Literal["all", "selected", "territory", "category"]


class LinkCreate(BaseModel):
    slug: str | None = Field(None, pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    active: bool = True
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    max_logins: int | None = Field(None, ge=1)
    client_scope: Scope = "all"
    scope_values: list[str] = Field(default_factory=list, max_length=1000)
    settings: LinkSettings


class LinkUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    active: bool | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    max_logins: int | None = Field(None, ge=1)
    client_scope: Scope | None = None
    scope_values: list[str] | None = Field(None, max_length=1000)
    settings: LinkSettings | None = None


class ClientsChange(BaseModel):
    add: list[int] = Field(default_factory=list, max_length=2000)
    remove: list[int] = Field(default_factory=list, max_length=2000)


class CloneIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)


def _state(link: AccessLink, now: datetime) -> str:
    if link.archived_at is not None:
        return "archived"
    if not link.active:
        return "inactive"
    if link.starts_at is not None and link.starts_at > now:
        return "scheduled"
    if link.ends_at is not None and link.ends_at <= now:
        return "expired"
    if link.max_logins is not None and link.login_count >= link.max_logins:
        return "exhausted"
    return "active"


def link_out(link: AccessLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "slug": link.slug,
        "name": link.name,
        "description": link.description,
        "active": link.active,
        "state": _state(link, clock.utcnow()),
        "starts_at": link.starts_at,
        "ends_at": link.ends_at,
        "max_logins": link.max_logins,
        "login_count": link.login_count,
        "client_scope": link.client_scope,
        "scope_values": list(link.scope_values),
        "settings": link.settings,
        "created_by": link.created_by,
        "created_at": link.created_at,
        "updated_at": link.updated_at,
        "archived_at": link.archived_at,
    }


def _snapshot(link: AccessLink) -> dict[str, Any]:
    return {k: v for k, v in link_out(link).items() if k not in ("state", "updated_at")}


async def _get(db: AsyncSession, link_id: int) -> AccessLink:
    link = await db.get(AccessLink, link_id)
    if link is None:
        raise HTTPException(404, "Link not found")
    return link


async def _validate(
    db: AsyncSession,
    *,
    settings: LinkSettings,
    scope: str,
    scope_values: list[str],
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> None:
    problems = await validate_link_settings(db, settings)
    if scope in ("territory", "category") and not scope_values:
        problems.append(f"scope_values required for client_scope '{scope}'")
    if starts_at and ends_at and ends_at <= starts_at:
        problems.append("ends_at must be after starts_at")
    if problems:
        raise HTTPException(422, problems)


def slug_is_guessable(slug: str) -> bool:
    """Heuristic only (the admin may still choose it): repeated/numeric/common-word slugs."""
    body = slug.replace("-", "")
    if len(set(body)) <= 3 or body.isdigit():
        return True
    tokens = [t for t in slug.split("-") if t and not t.isdigit()]
    return bool(tokens) and all(t in GUESSABLE_WORDS for t in tokens)


def _new_slug() -> str:
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(8))


async def _insert(db: AsyncSession, link: AccessLink, *, custom_slug: bool) -> None:
    """Flush a new link, retrying random slugs on collision."""
    for _ in range(1 if custom_slug else 8):
        try:
            async with db.begin_nested():
                db.add(link)
                await db.flush()
            return
        except IntegrityError as exc:
            if link in db:
                db.expunge(link)
            if custom_slug:
                raise HTTPException(409, "Slug already in use") from exc
            link.slug = _new_slug()
    raise HTTPException(500, "Could not allocate a slug")


@router.get("")
async def list_links(
    ctx: ReadDep,
    db: DbDep,
    active: bool | None = None,
    archived: bool = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    conds: list[ColumnElement[bool]] = [
        AccessLink.archived_at.is_not(None) if archived else AccessLink.archived_at.is_(None)
    ]
    if active is not None:
        conds.append(AccessLink.active.is_(active))
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append(or_(AccessLink.name.ilike(like), AccessLink.slug.ilike(like)))
    total = await db.scalar(select(func.count()).select_from(AccessLink).where(*conds))
    rows = (
        await db.scalars(
            select(AccessLink)
            .where(*conds)
            .order_by(AccessLink.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {"total": total, "items": [link_out(r) for r in rows]}


@router.get("/{link_id}")
async def get_link(link_id: int, ctx: ReadDep, db: DbDep) -> dict[str, Any]:
    return link_out(await _get(db, link_id))


@router.post("", status_code=201)
async def create_link(body: LinkCreate, ctx: WriteDep, db: DbDep, meta: MetaDep) -> dict[str, Any]:
    await _validate(
        db,
        settings=body.settings,
        scope=body.client_scope,
        scope_values=body.scope_values,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
    )
    link = AccessLink(
        slug=body.slug or _new_slug(),
        name=body.name,
        description=body.description,
        active=body.active,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        max_logins=body.max_logins,
        login_count=0,
        client_scope=body.client_scope,
        scope_values=body.scope_values if body.client_scope in ("territory", "category") else [],
        settings=body.settings.model_dump(mode="json"),
        created_by=ctx.user.id,
    )
    await _insert(db, link, custom_slug=body.slug is not None)
    await db.refresh(link)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="link.create",
        entity_type="access_link",
        entity_id=link.id,
        after=_snapshot(link),
        meta=meta,
    )
    await db.commit()
    out = link_out(link)
    if body.slug and slug_is_guessable(body.slug):
        out["warnings"] = ["slug_low_entropy"]  # advisory: anyone can guess this link URL
    return out


@router.patch("/{link_id}")
async def update_link(
    link_id: int, body: LinkUpdate, ctx: WriteDep, db: DbDep, meta: MetaDep
) -> dict[str, Any]:
    link = await _get(db, link_id)
    before = _snapshot(link)
    fields = body.model_fields_set
    try:
        settings = body.settings or LinkSettings.model_validate(link.settings)
    except ValidationError:
        raise HTTPException(422, "Stored settings are invalid; send full settings") from None
    scope = body.client_scope or link.client_scope
    scope_values = body.scope_values if body.scope_values is not None else list(link.scope_values)
    starts_at = body.starts_at if "starts_at" in fields else link.starts_at
    ends_at = body.ends_at if "ends_at" in fields else link.ends_at
    await _validate(
        db,
        settings=settings,
        scope=scope,
        scope_values=scope_values,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    if body.name is not None:
        link.name = body.name
    if "description" in fields:
        link.description = body.description
    if body.active is not None:
        link.active = body.active
    link.starts_at = starts_at
    link.ends_at = ends_at
    if "max_logins" in fields:
        link.max_logins = body.max_logins
    link.client_scope = scope
    link.scope_values = scope_values if scope in ("territory", "category") else []
    link.settings = settings.model_dump(mode="json")
    await db.flush()
    await db.refresh(link)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="link.update",
        entity_type="access_link",
        entity_id=link.id,
        before=before,
        after=_snapshot(link),
        meta=meta,
    )
    await db.commit()
    return link_out(link)


async def _set_archived(
    link_id: int, archived: bool, ctx: AdminContext, db: AsyncSession, meta: audit.RequestMeta
) -> dict[str, Any]:
    link = await _get(db, link_id)
    before = {"archived_at": link.archived_at}
    link.archived_at = clock.utcnow() if archived else None
    await db.flush()
    await db.refresh(link)  # server-side updated_at
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="link.archive" if archived else "link.unarchive",
        entity_type="access_link",
        entity_id=link.id,
        before=before,
        after={"archived_at": link.archived_at},
        meta=meta,
    )
    await db.commit()
    return link_out(link)


@router.post("/{link_id}/archive")
async def archive_link(link_id: int, ctx: WriteDep, db: DbDep, meta: MetaDep) -> dict[str, Any]:
    return await _set_archived(link_id, True, ctx, db, meta)


@router.post("/{link_id}/unarchive")
async def unarchive_link(link_id: int, ctx: WriteDep, db: DbDep, meta: MetaDep) -> dict[str, Any]:
    return await _set_archived(link_id, False, ctx, db, meta)


@router.post("/{link_id}/clone", status_code=201)
async def clone_link(
    link_id: int, body: CloneIn, ctx: WriteDep, db: DbDep, meta: MetaDep
) -> dict[str, Any]:
    src = await _get(db, link_id)
    clone = AccessLink(
        slug=_new_slug(),
        name=body.name or f"{src.name} (copy)"[:200],
        description=src.description,
        active=src.active,
        starts_at=src.starts_at,
        ends_at=src.ends_at,
        max_logins=src.max_logins,
        login_count=0,
        client_scope=src.client_scope,
        scope_values=list(src.scope_values),
        settings=src.settings,
        created_by=ctx.user.id,
    )
    await _insert(db, clone, custom_slug=False)
    client_ids = (
        await db.scalars(select(LinkClient.client_id).where(LinkClient.link_id == src.id))
    ).all()
    if client_ids:
        await db.execute(
            pg_insert(LinkClient)
            .values([{"link_id": clone.id, "client_id": c} for c in client_ids])
            .on_conflict_do_nothing()
        )
    await db.refresh(clone)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="link.clone",
        entity_type="access_link",
        entity_id=clone.id,
        after={**_snapshot(clone), "cloned_from": src.id, "clients": len(client_ids)},
        meta=meta,
    )
    await db.commit()
    return link_out(clone)


@router.get("/{link_id}/clients")
async def list_link_clients(
    link_id: int,
    ctx: ReadDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    await _get(db, link_id)
    total = await db.scalar(
        select(func.count()).select_from(LinkClient).where(LinkClient.link_id == link_id)
    )
    rows = (
        await db.execute(
            select(SdClient.id, SdClient.sd_id, SdClient.name)
            .join(LinkClient, LinkClient.client_id == SdClient.id)
            .where(LinkClient.link_id == link_id)
            .order_by(SdClient.name, SdClient.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return {
        "total": total,
        "items": [{"id": i, "sd_id": s, "name": n} for i, s, n in rows],
    }


@router.put("/{link_id}/clients")
async def change_link_clients(
    link_id: int, body: ClientsChange, ctx: WriteDep, db: DbDep, meta: MetaDep
) -> dict[str, int]:
    await _get(db, link_id)
    add, remove = sorted(set(body.add)), sorted(set(body.remove))
    if set(add) & set(remove):
        raise HTTPException(422, "A client cannot be in both add and remove")
    if add:
        found = set((await db.scalars(select(SdClient.id).where(SdClient.id.in_(add)))).all())
        missing = [c for c in add if c not in found]
        if missing:
            raise HTTPException(422, f"unknown client ids: {missing[:20]}")
        await db.execute(
            pg_insert(LinkClient)
            .values([{"link_id": link_id, "client_id": c} for c in add])
            .on_conflict_do_nothing()
        )
    removed = 0
    if remove:
        res = await db.execute(
            delete(LinkClient).where(
                LinkClient.link_id == link_id, LinkClient.client_id.in_(remove)
            )
        )
        removed = res.rowcount  # type: ignore[attr-defined]
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="link.clients_change",
        entity_type="access_link",
        entity_id=link_id,
        after={"added": add[:200], "removed": remove[:200], "added_count": len(add)},
        meta=meta,
    )
    await db.commit()
    total = await db.scalar(
        select(func.count()).select_from(LinkClient).where(LinkClient.link_id == link_id)
    )
    return {"added": len(add), "removed": removed, "total": total or 0}


STATS_MAX_DAYS = 366


@router.get("/{link_id}/stats")
async def link_stats(
    link_id: int,
    ctx: ReadDep,
    db: DbDep,
    days: Annotated[int, Query(ge=1, le=STATS_MAX_DAYS)] = 30,
) -> dict[str, Any]:
    """Usage of one link: logins (shop sessions), unique shops, orders and sums per day."""
    link = await _get(db, link_id)
    now = clock.utcnow()
    today = now.astimezone(SD_TZ).date()
    since = datetime.combine(today - timedelta(days=days - 1), time.min, SD_TZ)
    active_sessions = await db.scalar(
        select(func.count())
        .select_from(ShopSession)
        .where(
            ShopSession.link_id == link_id,
            ShopSession.revoked_at.is_(None),
            ShopSession.expires_at > now,
        )
    )
    unique_clients = await db.scalar(
        select(func.count(func.distinct(ShopSession.client_id))).where(
            ShopSession.link_id == link_id
        )
    )
    tz: ColumnElement[str] = literal_column(f"'{SD_TZ.key}'")
    fmt: ColumnElement[str] = literal_column("'YYYY-MM-DD'")
    s_day = func.to_char(func.timezone(tz, ShopSession.created_at), fmt)
    logins: dict[str, int] = dict(
        (
            await db.execute(
                select(s_day, func.count())
                .where(ShopSession.link_id == link_id, ShopSession.created_at >= since)
                .group_by(s_day)
            )
        ).all()
    )
    o_time = func.coalesce(ShopOrder.submitted_at, ShopOrder.created_at)
    o_day = func.to_char(func.timezone(tz, o_time), fmt)
    placed = ShopOrder.state.in_(PLACED)
    per_day = {
        d: (n, total)
        for d, n, total in (
            await db.execute(
                select(o_day, func.count(), func.coalesce(func.sum(ShopOrder.total_sum), 0))
                .where(ShopOrder.link_id == link_id, placed, o_time >= since)
                .group_by(o_day)
            )
        ).all()
    }
    by_state = {
        state: {"orders": n, "sum": num(total)}
        for state, n, total in (
            await db.execute(
                select(
                    ShopOrder.state, func.count(), func.coalesce(func.sum(ShopOrder.total_sum), 0)
                )
                .where(ShopOrder.link_id == link_id, o_time >= since)
                .group_by(ShopOrder.state)
            )
        ).all()
    }
    all_n, all_sum = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(ShopOrder.total_sum), 0)).where(
                ShopOrder.link_id == link_id, placed
            )
        )
    ).one()
    top_clients = [
        {"client_id": cid, "name": name, "orders": n, "sum": num(total)}
        for cid, name, n, total in (
            await db.execute(
                select(
                    SdClient.id,
                    SdClient.name,
                    func.count(),
                    func.coalesce(func.sum(ShopOrder.total_sum), 0),
                )
                .join(ShopOrder, ShopOrder.client_id == SdClient.id)
                .where(ShopOrder.link_id == link_id, placed, o_time >= since)
                .group_by(SdClient.id, SdClient.name)
                .order_by(func.sum(ShopOrder.total_sum).desc(), SdClient.id)
                .limit(10)
            )
        ).all()
    ]
    daily = []
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        n, total = per_day.get(d, (0, 0))
        daily.append({"date": d, "logins": logins.get(d, 0), "orders": n, "sum": num(total)})
    period_orders = sum(r["orders"] for r in daily)
    return {
        "login_count": link.login_count,
        "max_logins": link.max_logins,
        "active_sessions": active_sessions or 0,
        "unique_clients": unique_clients or 0,
        "orders": {
            "all_time": {"orders": all_n, "sum": num(all_sum)},
            "period": {
                "days": days,
                "orders": period_orders,
                "sum": num(sum((total for _n, total in per_day.values()), Decimal(0))),
                "logins": sum(r["logins"] for r in daily),
            },
            "by_state": by_state,
        },
        "daily": daily,
        "top_clients": top_clients,
    }

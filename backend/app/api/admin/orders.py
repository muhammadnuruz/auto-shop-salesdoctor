"""Admin orders API: list/detail/export, edit (re-sends to SD), status, resend, delete."""

import io
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminContext, DbDep, MetaDep, ServicesDep, require
from app.api.schemas import AdminOrderListOut, AdminOrderOut
from app.core.xlsx import cell
from app.domain.quantity import MAX_BLOCKS, MAX_PIECES
from app.integrations.salesdoctor import SDError
from app.models import AccessLink, SdClient, ShopOrder, ShopOrderEvent
from app.models.orders import ORDER_STATES
from app.services import audit
from app.services import orders as svc
from app.services.eligibility import parse_settings
from app.services.order_flow import (
    AdminEditOutcome,
    OrderLock,
    admin_change_status,
    discard_order,
    order_lock,
    push_after_admin_edit,
    reconcile_order,
    submit_order,
)
from app.services.orders import Actor, LineInput, problem
from app.services.stock_fresh import apply_to_mirror, fetch_warehouse_stock
from app.sync.common import SD_TZ

router = APIRouter(prefix="/orders", tags=["admin-orders"])
ReadDep = Annotated[AdminContext, Depends(require("orders.read"))]
WriteDep = Annotated[AdminContext, Depends(require("orders.write"))]
CancelDep = Annotated[AdminContext, Depends(require("orders.cancel"))]

EXPORT_LIMIT = 10_000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EDITABLE_ADMIN_STATES = frozenset({"draft", "failed", "sent"})


class AdminLineIn(BaseModel):
    product_ref: str = Field(min_length=1, max_length=64)
    blocks: int = Field(0, ge=0, le=MAX_BLOCKS)
    pieces: Decimal = Field(Decimal(0), ge=0, le=MAX_PIECES, decimal_places=3)
    comment: str | None = Field(None, max_length=svc.MAX_COMMENT)


class OrderPatch(BaseModel):
    version: int = Field(ge=1)
    agent: str | None = Field(None, max_length=64)
    warehouse: str | None = Field(None, max_length=64)
    price_type: str | None = Field(None, max_length=64)
    trade: str | None = Field(None, max_length=64)
    shipment_date: date | None = None
    comment: str | None = Field(None, max_length=svc.MAX_COMMENT)
    lines: list[AdminLineIn] | None = Field(None, max_length=svc.MAX_LINES)
    # Existing lines keep their stored price unless the price type changes or this is true;
    # lines added by this edit always get the current price.
    reprice: bool = False


class StatusIn(BaseModel):
    status: int = Field(ge=1, le=5)
    shipment_date: date | None = None
    version: int | None = Field(None, ge=1)


class VersionIn(BaseModel):
    version: int | None = Field(None, ge=1)


# --------------------------------------------------------------------------------- filters


def _filters(
    state: list[str] | None,
    sd_status: list[int] | None,
    client: int | None,
    link: int | None,
    warehouse: str | None,
    agent: str | None,
    date_from: date | None,
    date_to: date | None,
    q: str | None,
) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if state:
        bad = [s for s in state if s not in ORDER_STATES]
        if bad:
            raise HTTPException(422, f"unknown state: {bad[0]}")
        conds.append(ShopOrder.state.in_(state))
    if sd_status:
        conds.append(ShopOrder.sd_status.in_(sd_status))
    if client is not None:
        conds.append(ShopOrder.client_id == client)
    if link is not None:
        conds.append(ShopOrder.link_id == link)
    if warehouse:
        conds.append(ShopOrder.warehouse_sd_id == warehouse)
    if agent:
        conds.append(ShopOrder.agent_sd_id == agent)
    if date_from:
        conds.append(ShopOrder.created_at >= datetime.combine(date_from, time.min, SD_TZ))
    if date_to:
        conds.append(
            ShopOrder.created_at < datetime.combine(date_to + timedelta(days=1), time.min, SD_TZ)
        )
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conds.append(
            or_(
                ShopOrder.code_1c.ilike(like),
                ShopOrder.sd_id == q,
                ShopOrder.client_id.in_(select(SdClient.id).where(SdClient.name.ilike(like))),
            )
        )
    return conds


class Filters:
    """Query parameters shared by list and export."""

    def __init__(
        self,
        state: Annotated[list[str] | None, Query()] = None,
        sd_status: Annotated[list[int] | None, Query()] = None,
        client: Annotated[int | None, Query()] = None,
        link: Annotated[int | None, Query()] = None,
        warehouse: Annotated[str | None, Query(max_length=64)] = None,
        agent: Annotated[str | None, Query(max_length=64)] = None,
        date_from: date | None = None,
        date_to: date | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
    ) -> None:
        self.conds = _filters(
            state, sd_status, client, link, warehouse, agent, date_from, date_to, q
        )


async def _rows(
    db: AsyncSession, conds: list[ColumnElement[bool]], limit: int, offset: int
) -> list[Any]:
    return list(
        (
            await db.execute(
                select(ShopOrder, SdClient.name, AccessLink.slug, AccessLink.name)
                .join(SdClient, SdClient.id == ShopOrder.client_id)
                .join(AccessLink, AccessLink.id == ShopOrder.link_id)
                .where(*conds)
                .order_by(ShopOrder.created_at.desc(), ShopOrder.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )


def _row_out(
    order: ShopOrder,
    client_name: str | None,
    slug: str,
    link_name: str,
    names: dict[str, dict[str, str | None]],
) -> dict[str, Any]:
    return {
        **svc.order_summary(order, names),
        "client": {"id": order.client_id, "name": client_name},
        "link": {"id": order.link_id, "slug": slug, "name": link_name},
    }


# ----------------------------------------------------------------------------------- list


@router.get("", response_model=AdminOrderListOut, response_model_exclude_unset=True)
async def list_orders(
    ctx: ReadDep,
    db: DbDep,
    f: Annotated[Filters, Depends()],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    count, total_sum, total_qty = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(ShopOrder.total_sum), 0),
                func.coalesce(func.sum(ShopOrder.total_qty), 0),
            ).where(*f.conds)
        )
    ).one()
    rows = await _rows(db, f.conds, limit, offset)
    names = await svc.reference_names(db, [r[0] for r in rows])
    return {
        "total": count,
        "summary": {
            "count": count,
            "total_sum": svc.num(total_sum),
            "total_qty": svc.num(total_qty),
        },
        "items": [_row_out(o, cn, slug, ln, names) for o, cn, slug, ln in rows],
    }


@router.get("/export.xlsx")
async def export_orders(
    ctx: ReadDep,
    db: DbDep,
    meta: MetaDep,
    f: Annotated[Filters, Depends()],
) -> Response:
    from openpyxl import Workbook

    rows = await _rows(db, f.conds, EXPORT_LIMIT, 0)
    names = await svc.reference_names(db, [r[0] for r in rows])

    safe = cell

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("orders")
    ws.append(
        [
            "Code", "SD id", "State", "SD status", "Client", "Link", "Agent", "Warehouse",
            "Price type", "Trade", "Lines", "Qty", "Sum", "Created", "Sent", "Comment",
        ]
    )  # fmt: skip
    for o, client_name, slug, _link_name in rows:
        ws.append(
            [
                safe(o.code_1c),
                safe(o.sd_id),
                o.state,
                o.sd_status,
                safe(client_name),
                safe(slug),
                safe(names["agent"].get(o.agent_sd_id or "")),
                safe(names["warehouse"].get(o.warehouse_sd_id or "")),
                safe(names["price_type"].get(o.price_type_sd_id or "")),
                safe(names["trade"].get(o.trade_sd_id or "")),
                o.lines_count,
                svc.num(o.total_qty),
                svc.num(o.total_sum),
                o.created_at.astimezone(SD_TZ).replace(tzinfo=None),
                o.sent_at.astimezone(SD_TZ).replace(tzinfo=None) if o.sent_at else None,
                safe(o.comment),
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="order.export",
        entity_type="shop_order",
        after={"rows": len(rows)},
        meta=meta,
    )
    await db.commit()
    return Response(
        buf.getvalue(),
        media_type=XLSX,
        headers={
            "Content-Disposition": 'attachment; filename="orders.xlsx"',
            "X-Rows": str(len(rows)),
        },
    )


# --------------------------------------------------------------------------------- detail


async def _detail(
    db: AsyncSession, order: ShopOrder, view: svc.OrderView | None = None
) -> dict[str, Any]:
    """Admin order document; ``view`` (preview) is rendered as given instead of re-read."""
    client = await db.get(SdClient, order.client_id)
    link = await db.get(AccessLink, order.link_id)
    assert client is not None and link is not None  # noqa: S101 (RESTRICT FKs)
    if view is None:
        out = await svc.render_order(db, order, client, admin=True)
    else:
        out = await svc.order_detail(db, view, await svc.admin_options(db), client, admin=True)
    events = (
        await db.scalars(
            select(ShopOrderEvent)
            .where(ShopOrderEvent.order_id == order.id)
            .order_by(ShopOrderEvent.at, ShopOrderEvent.id)
        )
    ).all()
    out["client"] = {"id": client.id, "sd_id": client.sd_id, "name": client.name}
    out["link"] = {"id": link.id, "slug": link.slug, "name": link.name}
    out["created_by_admin_id"] = order.created_by_admin_id
    out["submitted_at"] = order.submitted_at
    out["events"] = [
        {
            "at": e.at,
            "actor_type": e.actor_type,
            "actor_id": e.actor_id,
            "event": e.event,
            "payload": e.payload,
        }
        for e in events
    ]
    return out


@router.get("/{public_id}", response_model=AdminOrderOut, response_model_exclude_unset=True)
async def get_order(public_id: uuid.UUID, ctx: ReadDep, db: DbDep) -> dict[str, Any]:
    order = await svc.get_order(db, public_id)
    return await _detail(db, order)


# ---------------------------------------------------------------------------------- edit


def _header_snapshot(order: ShopOrder) -> dict[str, Any]:
    return {
        **{name: getattr(order, col) for name, col in svc.HEADER_FIELDS.items()},
        "shipment_date": order.shipment_date.isoformat() if order.shipment_date else None,
        "comment": order.comment,
    }


def _lines_snapshot(view: svc.OrderView) -> list[dict[str, Any]]:
    return [
        {
            "product_ref": v.product.sd_id,
            "qty": svc.num(v.line.quantity),
            "price": svc.num(v.line.price),
        }
        for v in view.lines
    ]


def _outcome_response(out: AdminEditOutcome, body: dict[str, Any] | None) -> JSONResponse:
    if out.kind in ("sent", "saved"):
        return JSONResponse(jsonable_encoder(body), status_code=200)
    if out.kind == "unknown":
        return JSONResponse(jsonable_encoder(body), status_code=202)
    code = "sd_rejected" if out.kind == "rejected" else "sd_unavailable"
    return JSONResponse(
        {"detail": {"code": code, "errors": out.errors}},
        status_code=422 if out.kind == "rejected" else 502,
    )


@router.patch(
    "/{public_id}",
    response_model=AdminOrderOut,
    response_model_exclude_unset=True,
    responses={202: {"model": AdminOrderOut}},
)
async def edit_order(
    public_id: uuid.UUID,
    body: OrderPatch,
    ctx: WriteDep,
    db: DbDep,
    flow: ServicesDep,
    meta: MetaDep,
) -> JSONResponse:
    """Edit header and/or lines. A sent order is re-sent to SD (same code_1C) before commit."""
    order = await svc.get_order(db, public_id)
    async with order_lock(flow.redis, order.id) as lock:
        return await _edit_locked(db, flow, lock, public_id, body, ctx, meta)


def _ensure_admin_editable(order: ShopOrder) -> None:
    if order.state not in EDITABLE_ADMIN_STATES or (
        order.state == "sent" and order.sd_status not in (None, 1)
    ):
        raise problem(409, "order_not_editable", state=order.state, sd_status=order.sd_status)


def _price_flags(
    view: svc.OrderView, old_prices: dict[int, Decimal], reprice: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(repriced, price_drift): lines whose price changed in this edit / kept lines whose stored
    price differs from the current price list."""
    repriced: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    for v in view.lines:
        old = old_prices.get(v.product.id)
        if reprice:
            if old is not None and old != v.line.price:
                repriced.append(
                    {
                        "product_ref": v.product.sd_id,
                        "old": svc.num(old),
                        "new": svc.num(v.line.price),
                    }
                )
        elif old is not None and v.mirror_price is not None and v.mirror_price != v.line.price:
            drift.append(
                {
                    "product_ref": v.product.sd_id,
                    "kept_price": svc.num(v.line.price),
                    "current_price": svc.num(v.mirror_price),
                }
            )
    return repriced, drift


async def _edit_locked(
    db: AsyncSession,
    flow: Any,
    lock: OrderLock,
    public_id: uuid.UUID,
    body: OrderPatch,
    ctx: AdminContext,
    meta: Any,
) -> JSONResponse:
    actor = Actor("admin", ctx.user.id)
    order = await svc.get_order(db, public_id, for_update=True)  # re-read under the lock
    _ensure_admin_editable(order)
    svc.check_version(order, body.version)
    client = await db.get(SdClient, order.client_id)
    link = await db.get(AccessLink, order.link_id)
    assert client is not None and link is not None  # noqa: S101
    st = parse_settings(link)
    options = await svc.admin_options(db)

    before_view = await svc.build_view(db, order, st=st, admin=True, live=False)
    before = {"header": _header_snapshot(order), "lines": _lines_snapshot(before_view)}
    old_warehouse = order.warehouse_sd_id
    old_price_type = order.price_type_sd_id
    old_prices = {v.line.product_id: v.line.price for v in before_view.lines}
    credit = {v.line.product_id: v.line.quantity for v in before_view.lines}

    fields = body.model_fields_set
    patch = {k: getattr(body, k) for k in svc.HEADER_FIELDS if k in fields}
    errors = svc.apply_header(order, patch, options)
    if errors:
        raise problem(422, "invalid_header", errors=errors)
    if "shipment_date" in fields:
        svc.validate_shipment_date(body.shipment_date)
        order.shipment_date = body.shipment_date
    if "comment" in fields:
        order.comment = body.comment or ""
    if body.lines is not None:
        inputs = [LineInput(i.product_ref, i.blocks, i.pieces, i.comment) for i in body.lines]
        await svc.replace_lines(db, order, inputs, st=st, admin=True)
    if order.warehouse_sd_id != old_warehouse or order.state != "sent":
        credit = {}  # the old reservation lives in another warehouse / does not exist
    reprice = body.reprice or order.price_type_sd_id != old_price_type
    fixed = None if reprice else old_prices
    view = await svc.touch(db, order, st=st, admin=True, credit=credit, fixed_prices=fixed)
    after = {"header": _header_snapshot(order), "lines": _lines_snapshot(view)}
    changes = {"before": before, "after": after}
    repriced, drift = _price_flags(view, old_prices, reprice)

    async def respond(out: AdminEditOutcome) -> JSONResponse:
        detail = await _detail(db, order) if out.kind in ("sent", "saved", "unknown") else None
        if detail is not None:
            detail["repriced"], detail["price_drift"] = repriced, drift
        return _outcome_response(out, detail)

    if order.state != "sent":
        svc.add_event(db, order, "admin_edit", actor, changes)
        audit.record(
            db,
            actor_type="admin",
            actor_id=ctx.user.id,
            action="order.admin_edit",
            entity_type="shop_order",
            entity_id=order.id,
            before=before,
            after=after,
            meta=meta,
        )
        await db.commit()
        return await respond(AdminEditOutcome("saved", view))

    # sent: validate like a submit (fresh stock, own reservation credited), then update SD
    assert order.warehouse_sd_id is not None  # noqa: S101
    await lock.renew()
    try:
        fresh = await fetch_warehouse_stock(flow.sd, order.warehouse_sd_id)
    except SDError as exc:
        await db.rollback()
        raise problem(503, "sd_unavailable", message="Sales Doctor is unavailable") from exc
    view = await svc.build_view(
        db,
        order,
        st=st,
        admin=True,
        stock_override=fresh,
        credit=credit,
        live=True,
        fixed_prices=fixed,
    )
    problems = svc.collect_problems(view, options, client, admin=True)
    if problems:
        await db.rollback()
        raise problem(422, "validation_failed", errors=problems)
    if fresh is not None:
        await apply_to_mirror(
            db, order.warehouse_sd_id, fresh, force_zero=[v.product.sd_id for v in view.lines]
        )
    outcome = await push_after_admin_edit(
        db, flow, order, view, client, st, actor, changes, meta, before, lock
    )
    return await respond(outcome)


@router.post(
    "/{public_id}/preview", response_model=AdminOrderOut, response_model_exclude_unset=True
)
async def preview_order(
    public_id: uuid.UUID, body: OrderPatch, ctx: WriteDep, db: DbDep
) -> dict[str, Any]:
    """Recalculate what PATCH would produce, without saving anything.

    Same body and rules as PATCH (version, editable states, header/line validation, reprice /
    price_drift) but: no lock, no version bump, no audit/event, no Sales Doctor call at all.
    Stock is the mirror's (no fresh SD fetch) with the order's own reservation credited, so
    ``problems`` can differ from the real save, which re-checks against fresh SD stock.
    """
    try:
        return await _preview(db, public_id, body)
    finally:
        await db.rollback()  # nothing was written; this just ends the read transaction


async def _preview(db: AsyncSession, public_id: uuid.UUID, body: OrderPatch) -> dict[str, Any]:
    order = await svc.get_order(db, public_id)
    _ensure_admin_editable(order)
    svc.check_version(order, body.version)
    link = await db.get(AccessLink, order.link_id)
    assert link is not None  # noqa: S101
    st = parse_settings(link)
    options = await svc.admin_options(db)

    before_view = await svc.build_view(db, order, st=st, admin=True, live=False)
    old_warehouse, old_price_type = order.warehouse_sd_id, order.price_type_sd_id
    old_prices = {v.line.product_id: v.line.price for v in before_view.lines}
    credit = {v.line.product_id: v.line.quantity for v in before_view.lines}
    existing = [(v.line, v.product) for v in before_view.lines]
    # Detach everything that is about to be mutated: a detached object is never flushed, so
    # no UPDATE/INSERT can reach the database even by accident.
    db.expunge(order)
    for line, _product in existing:
        db.expunge(line)

    fields = body.model_fields_set
    patch = {k: getattr(body, k) for k in svc.HEADER_FIELDS if k in fields}
    errors = svc.apply_header(order, patch, options)
    if errors:
        raise problem(422, "invalid_header", errors=errors)
    if "shipment_date" in fields:
        svc.validate_shipment_date(body.shipment_date)
        order.shipment_date = body.shipment_date
    if "comment" in fields:
        order.comment = body.comment or ""
    rows = existing
    if body.lines is not None:
        inputs = [LineInput(i.product_ref, i.blocks, i.pieces, i.comment) for i in body.lines]
        prepared = await svc.prepare_lines(db, order, inputs, st=st, admin=True)
        rows = [
            (svc.new_line(order, product, item, qty, pos), product)
            for pos, (product, item, qty) in enumerate(prepared, start=1)
        ]
    if order.warehouse_sd_id != old_warehouse or order.state != "sent":
        credit = {}  # the old reservation lives in another warehouse / does not exist
    reprice = body.reprice or order.price_type_sd_id != old_price_type
    view = await svc.build_view(
        db,
        order,
        st=st,
        admin=True,
        credit=credit,
        live=True,
        fixed_prices=None if reprice else old_prices,
        lines=rows,
    )
    out = await _detail(db, order, view)
    out["repriced"], out["price_drift"] = _price_flags(view, old_prices, reprice)
    return out


# --------------------------------------------------------------------------------- status


@router.post(
    "/{public_id}/status",
    response_model=AdminOrderOut,
    response_model_exclude_unset=True,
    responses={202: {"model": AdminOrderOut}},
)
async def change_status(
    public_id: uuid.UUID,
    body: StatusIn,
    ctx: WriteDep,
    db: DbDep,
    flow: ServicesDep,
    meta: MetaDep,
) -> JSONResponse:
    if body.status == 5 and "orders.cancel" not in ctx.permissions:
        raise HTTPException(403, "Forbidden")
    out = await admin_change_status(
        db,
        flow,
        public_id=public_id,
        status=body.status,
        shipment_date=body.shipment_date.isoformat() if body.shipment_date else None,
        expected_version=body.version,
        actor=Actor("admin", ctx.user.id),
        meta=meta,
    )
    result = out.result
    if result.kind == "sent":
        detail = await _detail(db, out.order)
        detail["warnings"] = out.warnings
        return JSONResponse(jsonable_encoder(detail))
    if result.kind == "unknown":  # state is outcome_unknown, reconcile queued
        detail = await _detail(db, out.order)
        detail["warnings"] = out.warnings
        return JSONResponse(jsonable_encoder(detail), status_code=202)
    code = "sd_rejected" if result.kind == "rejected" else "sd_unavailable"
    return JSONResponse(
        {"detail": {"code": code, "errors": result.errors, "message": result.message}},
        status_code=422 if result.kind == "rejected" else 502,
    )


# ------------------------------------------------------------------------ resend / delete


@router.post(
    "/{public_id}/resend",
    response_model=AdminOrderOut,
    response_model_exclude_unset=True,
    responses={202: {"model": AdminOrderOut}},
)
async def resend(
    public_id: uuid.UUID,
    ctx: WriteDep,
    db: DbDep,
    flow: ServicesDep,
    meta: MetaDep,
    body: VersionIn | None = None,
) -> JSONResponse:
    """failed -> full submit pipeline; outcome_unknown -> verify in SD, else resend (idempotent)."""
    order = await svc.get_order(db, public_id)
    actor = Actor("admin", ctx.user.id)
    if order.state == "failed":

        async def options_for(_c: Any, _l: Any, _s: Any) -> svc.HeaderOptions:
            return await svc.admin_options(db)

        result = await submit_order(
            db,
            flow,
            public_id=public_id,
            actor=actor,
            client_id=None,
            options_for=options_for,
            admin_mode=True,
            expected_version=body.version if body else None,
            meta=meta,
        )
        if result.kind in ("sent", "unknown"):
            return JSONResponse(
                jsonable_encoder(await _detail(db, result.order)),
                status_code=200 if result.kind == "sent" else 202,
            )
        code = "sd_rejected" if result.kind == "rejected" else "sd_unavailable"
        return JSONResponse(
            {"detail": {"code": code, "errors": result.errors}},
            status_code=422 if result.kind == "rejected" else 502,
        )
    if order.state == "outcome_unknown":
        outcome = await reconcile_order(db, flow, order.id, force=True, actor=actor, meta=meta)
        order = await svc.get_order(db, public_id)
        status = {"sent": 200, "failed": 422, "pending": 202, "exhausted": 502, "skipped": 409}[
            outcome
        ]
        out = await _detail(db, order)
        out["reconcile"] = outcome
        return JSONResponse(jsonable_encoder(out), status_code=status)
    raise problem(409, "invalid_state", state=order.state)


@router.delete(
    "/{public_id}",
    response_model=AdminOrderOut,
    response_model_exclude_unset=True,
    responses={202: {"model": AdminOrderOut}},
)
async def delete_order(
    public_id: uuid.UUID,
    ctx: CancelDep,
    db: DbDep,
    flow: ServicesDep,
    meta: MetaDep,
) -> JSONResponse:
    result, order = await discard_order(
        db,
        flow,
        public_id=public_id,
        actor=Actor("admin", ctx.user.id),
        client_id=None,
        hard=False,
        editable_only=False,
        expected_version=None,
        meta=meta,
    )
    if result.kind in ("sent", "unknown"):  # unknown: state is outcome_unknown, reconcile queued
        return JSONResponse(
            jsonable_encoder(await _detail(db, order)),
            status_code=200 if result.kind == "sent" else 202,
        )
    code = "sd_rejected" if result.kind == "rejected" else "sd_unavailable"
    return JSONResponse(
        {"detail": {"code": code, "errors": result.errors, "message": result.message}},
        status_code=422 if result.kind == "rejected" else 502,
    )

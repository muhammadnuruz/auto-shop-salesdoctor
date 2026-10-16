"""Shop orders API (prefix /api/shop/orders). Client-scoped: a shop only ever sees its own orders.

Every mutating call carries the order ``version`` (optimistic locking, 409 on mismatch) and the
response is always the full recalculated order (totals, per-line price/stock/warnings, category
summary). Prices are never accepted from the client.
"""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    DbDep,
    MetaDep,
    RedisDep,
    SdDep,
    ServicesDep,
    SettingsDep,
    ShopContext,
    ShopDep,
    check_origin,
    enforce_rate_limit,
)
from app.api.schemas import ImportPreviewOut, InvoiceOut, OrderOut, ShopOrderListOut
from app.core.ratelimit import Limit
from app.domain.quantity import MAX_BLOCKS, MAX_PIECES
from app.models import ProductBarcode, SdClient, SdProduct, ShopOrder, ShopOrderLine
from app.models.orders import ORDER_STATES
from app.services import catalog, order_import
from app.services import orders as svc
from app.services.invoice import build_invoice
from app.services.invoice_pdf import pdf_response
from app.services.order_flow import FlowResult, cancel_order, discard_order, submit_order
from app.services.orders import Actor, LineInput, problem
from app.sync.common import SD_TZ
from app.sync.products import sd_origin

router = APIRouter(
    prefix="/api/shop/orders", tags=["shop-orders"], dependencies=[Depends(check_origin)]
)

MAX_OPEN_DRAFTS = 30
MAX_UPLOAD_OVERHEAD = 64 * 1024  # multipart framing on top of the 2 MB file limit


# --------------------------------------------------------------------------------- schemas


class HeaderIn(BaseModel):
    agent: str | None = Field(None, max_length=64)
    warehouse: str | None = Field(None, max_length=64)
    price_type: str | None = Field(None, max_length=64)
    trade: str | None = Field(None, max_length=64)
    shipment_date: date | None = None
    comment: str | None = Field(None, max_length=svc.MAX_COMMENT)


class HeaderPatch(HeaderIn):
    version: int = Field(ge=1)


class LineIn(BaseModel):
    product_ref: str = Field(min_length=1, max_length=64)
    blocks: int = Field(0, ge=0, le=MAX_BLOCKS)
    pieces: Decimal = Field(Decimal(0), ge=0, le=MAX_PIECES, decimal_places=3)
    comment: str | None = Field(None, max_length=svc.MAX_COMMENT)


class LinesPut(BaseModel):
    version: int = Field(ge=1)
    lines: list[LineIn] = Field(max_length=svc.MAX_LINES)


class LinePatch(BaseModel):
    version: int = Field(ge=1)
    blocks: int | None = Field(None, ge=0, le=MAX_BLOCKS)
    pieces: Decimal | None = Field(None, ge=0, le=MAX_PIECES, decimal_places=3)
    comment: str | None = Field(None, max_length=svc.MAX_COMMENT)


class ScanIn(BaseModel):
    version: int = Field(ge=1)
    barcode: str | None = Field(None, min_length=1, max_length=64)
    product_ref: str | None = Field(None, min_length=1, max_length=64)
    pieces: int = Field(1, ge=1, le=1000)


class VersionIn(BaseModel):
    version: int | None = Field(None, ge=1)


class ImportApplyIn(BaseModel):
    order_id: uuid.UUID
    version: int = Field(ge=1)
    lines: list[LineIn] = Field(min_length=1, max_length=svc.MAX_LINES)


# ---------------------------------------------------------------------------------- helpers


def _actor(ctx: ShopContext) -> Actor:
    return Actor("shop", ctx.client.id)


async def _editable_order(
    db: DbDep, ctx: ShopContext, public_id: uuid.UUID, version: int | None
) -> tuple[ShopOrder, Any, svc.HeaderOptions]:
    """Lock + authorise an order for editing: own client, this link, draft/failed, version ok."""
    order = await svc.get_order(db, public_id, client_id=ctx.client.id, for_update=True)
    if order.link_id != ctx.link.id:
        raise problem(409, "order_other_link", message="Order belongs to another link")
    svc.ensure_editable(order)
    svc.check_version(order, version)
    st = svc.link_settings_or_409(ctx.link)
    options = await svc.link_options(db, ctx.client, st)
    return order, st, options


async def _finish_edit(
    db: AsyncSession, ctx: ShopContext, order: ShopOrder, st: Any, *, what: str
) -> dict[str, Any]:
    await svc.touch(db, order, st=st)
    if order.state != "draft":
        svc.add_event(db, order, "updated", _actor(ctx), {"what": what})
    await db.commit()
    return await svc.render_order(db, order, ctx.client)


def _flow_response(result: FlowResult, body: dict[str, Any]) -> JSONResponse:
    if result.kind == "sent":
        return JSONResponse(_json(body), status_code=200)
    if result.kind == "unknown":
        return JSONResponse(_json(body), status_code=202)
    code = "sd_rejected" if result.kind == "rejected" else "sd_unavailable"
    status = 422 if result.kind == "rejected" else 502
    return JSONResponse(
        {
            "detail": {
                "code": code,
                "errors": result.errors,
                "state": result.order.state,
                "message": (result.order.last_error or {}).get("message"),
            }
        },
        status_code=status,
    )


def _json(body: dict[str, Any]) -> Any:
    return jsonable_encoder(body)


# --------------------------------------------------------------------------- create / list


@router.post("", status_code=201, response_model=OrderOut, response_model_exclude_unset=True)
async def create_order(body: HeaderIn, ctx: ShopDep, db: DbDep) -> dict[str, Any]:
    st = svc.link_settings_or_409(ctx.link)
    open_drafts = await db.scalar(
        select(func.count())
        .select_from(ShopOrder)
        .where(ShopOrder.client_id == ctx.client.id, ShopOrder.state == "draft")
    )
    if (open_drafts or 0) >= MAX_OPEN_DRAFTS:
        raise problem(409, "too_many_drafts", message="Too many open drafts; send or delete some")
    svc.validate_shipment_date(body.shipment_date)
    options = await svc.link_options(db, ctx.client, st)
    header = {k: getattr(body, k) for k in svc.HEADER_FIELDS if k in body.model_fields_set}
    order = await svc.create_order(
        db,
        client=ctx.client,
        link=ctx.link,
        actor=_actor(ctx),
        options=options,
        header=header,
        shipment_date=body.shipment_date,
        comment=body.comment or "",
    )
    await db.commit()
    return await svc.render_order(db, order, ctx.client)


@router.get("", response_model=ShopOrderListOut, response_model_exclude_unset=True)
async def list_orders(
    ctx: ShopDep,
    db: DbDep,
    state: Annotated[list[str] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=40)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    conds: list[Any] = [ShopOrder.client_id == ctx.client.id, ShopOrder.state != "deleted"]
    if state:
        bad = [s for s in state if s not in ORDER_STATES]
        if bad:
            raise HTTPException(422, f"unknown state: {bad[0]}")
        conds.append(ShopOrder.state.in_(state))
    if date_from:
        conds.append(ShopOrder.created_at >= datetime.combine(date_from, time.min, SD_TZ))
    if date_to:
        conds.append(
            ShopOrder.created_at < datetime.combine(date_to + timedelta(days=1), time.min, SD_TZ)
        )
    if q:
        conds.append(ShopOrder.code_1c.ilike("%" + q.replace("%", "").replace("_", "") + "%"))
    total = await db.scalar(select(func.count()).select_from(ShopOrder).where(*conds))
    rows = (
        await db.scalars(
            select(ShopOrder)
            .where(*conds)
            .order_by(ShopOrder.created_at.desc(), ShopOrder.id.desc())
            .limit(size)
            .offset((page - 1) * size)
        )
    ).all()
    names = await svc.reference_names(db, list(rows))
    return {
        "items": [svc.order_summary(o, names) for o in rows],
        "total": total or 0,
        "page": page,
        "size": size,
    }


# --------------------------------------------------------------------------------- import
# (declared before the /{public_id} routes)


@router.post("/import", response_model=ImportPreviewOut)
async def import_preview(
    request: Request,
    ctx: ShopDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    order_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    await enforce_rate_limit(
        redis, [Limit(f"shopimport:{ctx.client.id}", settings.RL_SHOP_IMPORT_PER_MIN, 60)]
    )
    declared = request.headers.get("content-length")
    if (
        declared
        and declared.isdigit()
        and int(declared) > order_import.MAX_BYTES + MAX_UPLOAD_OVERHEAD
    ):
        raise problem(413, "file_too_large", message="File is larger than 2 MB")
    content = await file.read(order_import.MAX_BYTES + 1)
    order = await svc.get_order(db, order_id, client_id=ctx.client.id)
    svc.ensure_editable(order)
    st = svc.link_settings_or_409(ctx.link)
    cx = await catalog.context_from_order(db, order, st)
    try:
        # CPU-bound parsing of untrusted files: never on the event loop
        rows = await run_in_threadpool(order_import.parse_upload, file.filename or "", content)
    except order_import.ImportFileError as exc:
        status = 413 if exc.code == "file_too_large" else 422
        raise problem(status, exc.code, message=exc.message) from exc
    return await order_import.match_rows(db, cx, rows)


@router.post("/import/apply", response_model=OrderOut, response_model_exclude_unset=True)
async def import_apply(body: ImportApplyIn, ctx: ShopDep, db: DbDep) -> dict[str, Any]:
    """Add confirmed import lines to a draft; quantities of products already present add up."""
    order, st, _ = await _editable_order(db, ctx, body.order_id, body.version)
    merged: dict[str, LineInput] = {}
    existing = (
        await db.execute(
            select(ShopOrderLine, SdProduct.sd_id)
            .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
            .where(ShopOrderLine.order_id == order.id)
            .order_by(ShopOrderLine.position)
        )
    ).all()
    for line, ref in existing:
        merged[ref] = LineInput(ref, line.blocks, line.pieces, line.comment)
    for item in body.lines:
        cur = merged.get(item.product_ref)
        if cur is None:
            merged[item.product_ref] = LineInput(
                item.product_ref, item.blocks, item.pieces, item.comment
            )
        else:
            cur.blocks += item.blocks
            cur.pieces += item.pieces
    await svc.replace_lines(db, order, list(merged.values()), st=st)
    return await _finish_edit(db, ctx, order, st, what="import")


# ------------------------------------------------------------------------------ one order


@router.get("/{public_id}", response_model=OrderOut, response_model_exclude_unset=True)
async def get_order(public_id: uuid.UUID, ctx: ShopDep, db: DbDep) -> dict[str, Any]:
    order = await svc.get_order(db, public_id, client_id=ctx.client.id)
    return await svc.render_order(db, order, ctx.client)


@router.get("/{public_id}/invoice", response_model=InvoiceOut)
async def get_invoice(
    public_id: uuid.UUID, ctx: ShopDep, db: DbDep, sd: SdDep, redis: RedisDep
) -> dict[str, Any]:
    """Waybill of a sent order, read back from Sales Doctor (local copy when SD is down)."""
    order = await svc.get_order(db, public_id, client_id=ctx.client.id)
    return await build_invoice(db, sd, redis, order)


@router.get("/{public_id}/invoice.pdf")
async def get_invoice_pdf(
    public_id: uuid.UUID,
    ctx: ShopDep,
    db: DbDep,
    sd: SdDep,
    redis: RedisDep,
    settings: SettingsDep,
    lang: Literal["ru", "uz"] = "ru",
) -> Response:
    order = await svc.get_order(db, public_id, client_id=ctx.client.id)
    inv = await build_invoice(db, sd, redis, order)
    return await pdf_response(
        inv,
        sd_origin=sd_origin(settings.SD_BASE_URL),
        user_agent=settings.SD_USER_AGENT,
        lang=lang,
    )


@router.patch("/{public_id}", response_model=OrderOut, response_model_exclude_unset=True)
async def patch_header(
    public_id: uuid.UUID, body: HeaderPatch, ctx: ShopDep, db: DbDep
) -> dict[str, Any]:
    order, st, options = await _editable_order(db, ctx, public_id, body.version)
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
    svc.autofill_header(order, options)
    return await _finish_edit(db, ctx, order, st, what="header")


@router.delete("/{public_id}", status_code=204)
async def delete_draft(
    public_id: uuid.UUID,
    ctx: ShopDep,
    db: DbDep,
    flow: ServicesDep,
    meta: MetaDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    """Drafts / failed orders only. A never-submitted draft is removed locally; anything that
    was ever submitted is first looked up in SD by code_1C and cancelled/deleted there."""
    result, _order = await discard_order(
        db,
        flow,
        public_id=public_id,
        actor=_actor(ctx),
        client_id=ctx.client.id,
        hard=True,
        editable_only=True,
        expected_version=version,
        meta=meta,
    )
    if result.kind == "sent":
        return Response(status_code=204)
    if result.kind == "unknown":
        return JSONResponse(
            {"detail": {"code": "outcome_unknown", "message": result.message}}, status_code=202
        )
    code = "sd_rejected" if result.kind == "rejected" else "sd_unavailable"
    return JSONResponse(
        {"detail": {"code": code, "errors": result.errors}},
        status_code=422 if result.kind == "rejected" else 502,
    )


@router.put("/{public_id}/lines", response_model=OrderOut, response_model_exclude_unset=True)
async def put_lines(
    public_id: uuid.UUID, body: LinesPut, ctx: ShopDep, db: DbDep
) -> dict[str, Any]:
    order, st, _ = await _editable_order(db, ctx, public_id, body.version)
    inputs = [LineInput(i.product_ref, i.blocks, i.pieces, i.comment) for i in body.lines]
    await svc.replace_lines(db, order, inputs, st=st)
    return await _finish_edit(db, ctx, order, st, what="lines")


@router.post("/{public_id}/lines/scan", response_model=OrderOut, response_model_exclude_unset=True)
async def scan_line(public_id: uuid.UUID, body: ScanIn, ctx: ShopDep, db: DbDep) -> dict[str, Any]:
    """Scanner flow: +N pieces (default 1) of a barcode or product_ref."""
    if (body.barcode is None) == (body.product_ref is None):
        raise HTTPException(422, "Provide exactly one of barcode / product_ref")
    order, st, _ = await _editable_order(db, ctx, public_id, body.version)
    ref = body.product_ref
    if body.barcode is not None:
        cx = await catalog.context_from_order(db, order, st)
        found = await catalog.find_by_barcode(db, cx, body.barcode)
        if not found:
            exists = await db.scalar(
                select(func.count())
                .select_from(ProductBarcode)
                .where(ProductBarcode.barcode == body.barcode)
            )
            raise problem(
                404,
                "not_orderable" if exists else "barcode_not_found",
                message="Product cannot be ordered" if exists else "Barcode not found",
            )
        if len(found) > 1:
            raise problem(409, "ambiguous_barcode", candidates=found)
        ref = found[0]["product_ref"]
    assert ref is not None  # noqa: S101
    line = await db.scalar(
        select(ShopOrderLine)
        .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
        .where(ShopOrderLine.order_id == order.id, SdProduct.sd_id == ref)
    )
    blocks = line.blocks if line else 0
    pieces = (line.pieces if line else Decimal(0)) + body.pieces
    await svc.upsert_line(db, order, LineInput(ref, blocks, pieces, None), st=st)
    return await _finish_edit(db, ctx, order, st, what="scan")


@router.patch(
    "/{public_id}/lines/{product_ref}", response_model=OrderOut, response_model_exclude_unset=True
)
async def patch_line(
    public_id: uuid.UUID, product_ref: str, body: LinePatch, ctx: ShopDep, db: DbDep
) -> dict[str, Any]:
    """Add / update one line (quantity 0 removes it). Unset fields keep their current value."""
    order, st, _ = await _editable_order(db, ctx, public_id, body.version)
    line = await db.scalar(
        select(ShopOrderLine)
        .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
        .where(ShopOrderLine.order_id == order.id, SdProduct.sd_id == product_ref)
    )
    fields = body.model_fields_set
    blocks = (
        body.blocks
        if "blocks" in fields and body.blocks is not None
        else (line.blocks if line else 0)
    )
    pieces = (
        body.pieces
        if "pieces" in fields and body.pieces is not None
        else (line.pieces if line else Decimal(0))
    )
    comment = body.comment if "comment" in fields else None
    if line is None and not blocks and not pieces:
        raise problem(422, "zero_quantity", message="Quantity must be > 0")
    await svc.upsert_line(db, order, LineInput(product_ref, blocks, pieces, comment), st=st)
    return await _finish_edit(db, ctx, order, st, what="line")


@router.delete(
    "/{public_id}/lines/{product_ref}", response_model=OrderOut, response_model_exclude_unset=True
)
async def delete_line(
    public_id: uuid.UUID,
    product_ref: str,
    ctx: ShopDep,
    db: DbDep,
    version: Annotated[int, Query(ge=1)],
) -> dict[str, Any]:
    order, st, _ = await _editable_order(db, ctx, public_id, version)
    await svc.remove_line(db, order, product_ref)
    return await _finish_edit(db, ctx, order, st, what="line_removed")


# ------------------------------------------------------------------- submit / cancel / copy


@router.post(
    "/{public_id}/submit",
    response_model=OrderOut,
    response_model_exclude_unset=True,
    responses={202: {"model": OrderOut}},
)
async def submit(
    public_id: uuid.UUID,
    ctx: ShopDep,
    db: DbDep,
    flow: ServicesDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
    body: VersionIn | None = None,
) -> JSONResponse:
    await enforce_rate_limit(
        redis, [Limit(f"shopsubmit:{ctx.client.id}", settings.RL_SHOP_SUBMIT_PER_MIN, 60)]
    )
    st = svc.link_settings_or_409(ctx.link)

    async def options_for(client: SdClient, _link: Any, _st: Any) -> svc.HeaderOptions:
        return await svc.link_options(db, client, st)

    result = await submit_order(
        db,
        flow,
        public_id=public_id,
        actor=_actor(ctx),
        client_id=ctx.client.id,
        options_for=options_for,
        admin_mode=False,
        expected_version=body.version if body else None,
        meta=meta,
        link_id=ctx.link.id,
    )
    return _flow_response(result, await svc.render_order(db, result.order, ctx.client))


@router.post(
    "/{public_id}/cancel",
    response_model=OrderOut,
    response_model_exclude_unset=True,
    responses={202: {"model": OrderOut}},
)
async def cancel(
    public_id: uuid.UUID,
    ctx: ShopDep,
    db: DbDep,
    flow: ServicesDep,
    meta: MetaDep,
    body: VersionIn | None = None,
) -> JSONResponse:
    result = await cancel_order(
        db,
        flow,
        public_id=public_id,
        actor=_actor(ctx),
        client_id=ctx.client.id,
        expected_version=body.version if body else None,
        meta=meta,
    )
    if result.kind in ("sent", "unknown"):  # unknown: state is outcome_unknown, reconcile queued
        body = _json(await svc.render_order(db, result.order, ctx.client))
        return JSONResponse(body, status_code=200 if result.kind == "sent" else 202)
    code = "sd_rejected" if result.kind == "rejected" else "sd_unavailable"
    return JSONResponse(
        {"detail": {"code": code, "errors": result.errors}},
        status_code=422 if result.kind == "rejected" else 502,
    )


@router.post(
    "/{public_id}/duplicate",
    status_code=201,
    response_model=OrderOut,
    response_model_exclude_unset=True,
)
async def duplicate(public_id: uuid.UUID, ctx: ShopDep, db: DbDep) -> dict[str, Any]:
    """Repeat order: a new draft with the same header (where still allowed) and lines."""
    src = await svc.get_order(db, public_id, client_id=ctx.client.id)
    st = svc.link_settings_or_409(ctx.link)
    open_drafts = await db.scalar(
        select(func.count())
        .select_from(ShopOrder)
        .where(ShopOrder.client_id == ctx.client.id, ShopOrder.state == "draft")
    )
    if (open_drafts or 0) >= MAX_OPEN_DRAFTS:
        raise problem(409, "too_many_drafts", message="Too many open drafts; send or delete some")
    options = await svc.link_options(db, ctx.client, st)
    header = {
        name: getattr(src, column)
        for name, column in svc.HEADER_FIELDS.items()
        if getattr(src, column) in options.for_field(name)
    }
    new = await svc.create_order(
        db,
        client=ctx.client,
        link=ctx.link,
        actor=_actor(ctx),
        options=options,
        header=header,
        comment=src.comment,
    )
    rows = (
        await db.execute(
            select(ShopOrderLine, SdProduct.sd_id)
            .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
            .where(ShopOrderLine.order_id == src.id)
            .order_by(ShopOrderLine.position)
        )
    ).all()
    for line, ref in rows:  # products that became unorderable are simply skipped
        try:
            await svc.upsert_line(
                db, new, LineInput(ref, line.blocks, line.pieces, line.comment), st=st
            )
        except HTTPException:
            continue
    await svc.touch(db, new, st=st)
    await db.commit()
    return await svc.render_order(db, new, ctx.client)

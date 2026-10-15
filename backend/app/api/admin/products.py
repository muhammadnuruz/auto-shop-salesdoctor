"""Admin products API: browse mirror (stock/prices), hide from the shop, extra barcodes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminContext, DbDep, MetaDep, require
from app.api.schemas import Number
from app.models import (
    ProductBarcode,
    ProductSetting,
    SdPrice,
    SdPriceType,
    SdProduct,
    SdProductCategory,
    SdStock,
    SdWarehouse,
)
from app.services import audit
from app.services.orders import num, problem

router = APIRouter(prefix="/products", tags=["admin-products"])
ReadDep = Annotated[AdminContext, Depends(require("products.read"))]
WriteDep = Annotated[AdminContext, Depends(require("products.write"))]

BARCODE_PATTERN = r"^[0-9]{8,14}$"


class ProductPatch(BaseModel):
    hidden: bool | None = None
    note: str | None = Field(None, max_length=1000)


BULK_MAX = 10_000


class BulkHiddenIn(BaseModel):
    sd_ids: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=BULK_MAX
    )
    hidden: bool


class BulkHiddenOut(BaseModel):
    updated: int
    not_found: int


class ProductIdsOut(BaseModel):
    total: int
    sd_ids: list[str]


class BarcodeIn(BaseModel):
    barcode: str = Field(pattern=BARCODE_PATTERN)


class ProductBarcodeOut(BaseModel):
    barcode: str
    source: str


class ProductStockOut(BaseModel):
    warehouse: str
    name: str | None
    quantity: Number


class ProductPriceOut(BaseModel):
    price_type: str
    name: str | None
    price: Number


class ProductOut(BaseModel):
    sd_id: str
    code: str | None
    name: str | None
    active: bool
    hidden: bool
    note: str | None
    category: str | None
    category_name: str | None
    pack_quantity: Number | None
    image_url: str | None
    thumb_url: str | None
    barcodes: list[ProductBarcodeOut]
    stock: list[ProductStockOut]  # per warehouse (always present)
    prices: list[ProductPriceOut]  # per sale price type (always present)
    # only when ?price_type= / ?warehouse= is given on the list:
    price: Number | None = None  # price in that price type (null = not priced)
    stock_qty: Number | None = None  # quantity in that warehouse (0 = no stock row)


class ProductListOut(BaseModel):
    total: int
    items: list[ProductOut]


class OtherProduct(BaseModel):
    sd_id: str
    name: str | None


class ProductBarcodeAddOut(ProductOut):
    warnings: list[str]
    other_products: list[OtherProduct]


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def _items(
    db: AsyncSession,
    rows: list[Any],
    *,
    price_type: str | None = None,
    warehouse: str | None = None,
) -> list[dict[str, Any]]:
    pids = [r[0].id for r in rows]
    barcodes: dict[int, list[dict[str, str]]] = {}
    stock: dict[int, list[dict[str, Any]]] = {}
    prices: dict[int, list[dict[str, Any]]] = {}
    if pids:
        for pid, code, source in (
            await db.execute(
                select(ProductBarcode.product_id, ProductBarcode.barcode, ProductBarcode.source)
                .where(ProductBarcode.product_id.in_(pids))
                .order_by(ProductBarcode.id)
            )
        ).all():
            barcodes.setdefault(pid, []).append({"barcode": code, "source": source})
        for pid, wh_sd, wh_name, qty in (
            await db.execute(
                select(SdStock.product_id, SdWarehouse.sd_id, SdWarehouse.name, SdStock.quantity)
                .join(SdWarehouse, SdWarehouse.id == SdStock.warehouse_id)
                .where(SdStock.product_id.in_(pids))
                .order_by(SdWarehouse.name)
            )
        ).all():
            stock.setdefault(pid, []).append(
                {"warehouse": wh_sd, "name": wh_name, "quantity": num(qty)}
            )
        for pid, pt_sd, pt_name, price in (
            await db.execute(
                select(SdPrice.product_id, SdPriceType.sd_id, SdPriceType.name, SdPrice.price)
                .join(SdPriceType, SdPriceType.id == SdPrice.price_type_id)
                .where(SdPrice.product_id.in_(pids), SdPriceType.kind == "sale")
                .order_by(SdPriceType.name)
            )
        ).all():
            prices.setdefault(pid, []).append(
                {"price_type": pt_sd, "name": pt_name, "price": num(price)}
            )
    out = [
        {
            "sd_id": p.sd_id,
            "code": p.code_1c,
            "name": p.name,
            "active": p.active,
            "hidden": bool(hidden),
            "note": note,
            "category": p.category_sd_id,
            "category_name": cat,
            "pack_quantity": num(p.pack_quantity),
            "image_url": p.image_url,
            "thumb_url": p.thumb_url,
            "barcodes": barcodes.get(p.id, []),
            "stock": stock.get(p.id, []),
            "prices": prices.get(p.id, []),
        }
        for p, hidden, note, cat in rows
    ]
    if price_type is not None or warehouse is not None:
        for (p, *_), item in zip(rows, out, strict=True):
            if price_type is not None:
                item["price"] = next(
                    (x["price"] for x in prices.get(p.id, []) if x["price_type"] == price_type),
                    None,
                )
            if warehouse is not None:
                item["stock_qty"] = next(
                    (x["quantity"] for x in stock.get(p.id, []) if x["warehouse"] == warehouse), 0
                )
    return out


_COLUMNS = (SdProduct, ProductSetting.hidden, ProductSetting.note, SdProductCategory.name)


def _select() -> Any:
    return (
        select(*_COLUMNS)
        .select_from(SdProduct)
        .outerjoin(ProductSetting, ProductSetting.product_id == SdProduct.id)
        .outerjoin(SdProductCategory, SdProductCategory.sd_id == SdProduct.category_sd_id)
    )


def _filters(
    *,
    q: str | None,
    active: bool | None,
    hidden: bool | None,
    has_barcode: bool | None,
    category: str | None,
) -> list[ColumnElement[bool]]:
    """WHERE conditions shared by the list and the "select all matching" id query.
    Callers must outer-join ProductSetting (``hidden`` filter)."""
    conds: list[ColumnElement[bool]] = []
    if q:
        conds.append(
            or_(
                SdProduct.name.ilike(_like(q)),
                SdProduct.code_1c == q,
                SdProduct.sd_id == q,
                SdProduct.id.in_(
                    select(ProductBarcode.product_id).where(ProductBarcode.barcode == q)
                ),
            )
        )
    if active is not None:
        conds.append(SdProduct.active.is_(active))
    if hidden is not None:
        is_hidden = func.coalesce(ProductSetting.hidden, False)
        conds.append(is_hidden.is_(True) if hidden else is_hidden.is_(False))
    if has_barcode is not None:
        exists = SdProduct.id.in_(select(ProductBarcode.product_id))
        conds.append(exists if has_barcode else ~exists)
    if category:
        conds.append(SdProduct.category_sd_id == category)
    return conds


@router.get("", response_model=ProductListOut, response_model_exclude_unset=True)
async def list_products(
    ctx: ReadDep,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    active: bool | None = None,
    hidden: bool | None = None,
    has_barcode: bool | None = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    price_type: Annotated[str | None, Query(max_length=64)] = None,
    warehouse: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    if price_type is not None and not await db.scalar(
        select(SdPriceType.id).where(SdPriceType.sd_id == price_type, SdPriceType.kind == "sale")
    ):
        raise problem(422, "unknown_price_type", message="Unknown sale price type")
    if warehouse is not None and not await db.scalar(
        select(SdWarehouse.id).where(SdWarehouse.sd_id == warehouse)
    ):
        raise problem(422, "unknown_warehouse", message="Unknown warehouse")
    conds = _filters(q=q, active=active, hidden=hidden, has_barcode=has_barcode, category=category)
    total = await db.scalar(
        select(func.count())
        .select_from(SdProduct)
        .outerjoin(ProductSetting, ProductSetting.product_id == SdProduct.id)
        .where(and_(True, *conds))
    )
    rows = (
        await db.execute(
            _select()
            .where(*conds)
            .order_by(SdProduct.name, SdProduct.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = await _items(db, list(rows), price_type=price_type, warehouse=warehouse)
    return {"total": total or 0, "items": items}


@router.get("/ids", response_model=ProductIdsOut)
async def list_product_ids(
    ctx: ReadDep,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    active: bool | None = None,
    hidden: bool | None = None,
    has_barcode: bool | None = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """SD ids of every product matching the list filters ("select all" across pages)."""
    conds = _filters(q=q, active=active, hidden=hidden, has_barcode=has_barcode, category=category)
    ids = (
        await db.scalars(
            select(SdProduct.sd_id)
            .outerjoin(ProductSetting, ProductSetting.product_id == SdProduct.id)
            .where(*conds)
            .order_by(SdProduct.name, SdProduct.id)
            .limit(BULK_MAX)
        )
    ).all()
    return {"total": len(ids), "sd_ids": list(ids)}


@router.post("/bulk", response_model=BulkHiddenOut)
async def bulk_hidden(
    body: BulkHiddenIn, ctx: WriteDep, db: DbDep, meta: MetaDep
) -> dict[str, Any]:
    """Hide or show many products in the shop catalog at once (one audit entry)."""
    wanted = list(dict.fromkeys(body.sd_ids))
    found = (
        await db.execute(select(SdProduct.id, SdProduct.sd_id).where(SdProduct.sd_id.in_(wanted)))
    ).all()
    if found:
        stmt = pg_insert(ProductSetting).values(
            [
                {"product_id": pid, "hidden": body.hidden, "updated_by": ctx.user.id}
                for pid, _ in found
            ]
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[ProductSetting.product_id],
                set_={
                    "hidden": stmt.excluded.hidden,
                    "updated_by": stmt.excluded.updated_by,
                    "updated_at": func.now(),
                },
            )
        )
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="product.bulk_update",
        entity_type="product",
        entity_id="bulk",
        after={
            "hidden": body.hidden,
            "count": len(found),
            "sd_ids": [sd for _, sd in found][:200],
        },
        meta=meta,
    )
    await db.commit()
    return {"updated": len(found), "not_found": len(wanted) - len(found)}


async def _product(db: AsyncSession, sd_id: str) -> SdProduct:
    product = await db.scalar(select(SdProduct).where(SdProduct.sd_id == sd_id))
    if product is None:
        raise HTTPException(404, "Product not found")
    return product


async def _one(db: AsyncSession, product: SdProduct) -> dict[str, Any]:
    row = (await db.execute(_select().where(SdProduct.id == product.id))).one()
    return (await _items(db, [row]))[0]


SdIdPath = Annotated[str, Path(min_length=1, max_length=64)]


@router.patch("/{sd_id}", response_model=ProductOut, response_model_exclude_unset=True)
async def patch_product(
    sd_id: SdIdPath, body: ProductPatch, ctx: WriteDep, db: DbDep, meta: MetaDep
) -> dict[str, Any]:
    product = await _product(db, sd_id)
    setting = await db.get(ProductSetting, product.id, with_for_update=True)
    before = {"hidden": setting.hidden, "note": setting.note} if setting else None
    if setting is None:
        setting = ProductSetting(product_id=product.id, hidden=False)
        db.add(setting)
    fields = body.model_fields_set
    if "hidden" in fields and body.hidden is not None:
        setting.hidden = body.hidden
    if "note" in fields:
        setting.note = body.note or None
    setting.updated_by = ctx.user.id
    await db.flush()
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="product.update",
        entity_type="product",
        entity_id=sd_id,
        before=before,
        after={"hidden": setting.hidden, "note": setting.note},
        meta=meta,
    )
    await db.commit()
    return await _one(db, product)


@router.post(
    "/{sd_id}/barcodes",
    status_code=201,
    response_model=ProductBarcodeAddOut,
    response_model_exclude_unset=True,
)
async def add_barcode(
    sd_id: SdIdPath, body: BarcodeIn, ctx: WriteDep, db: DbDep, meta: MetaDep
) -> dict[str, Any]:
    product = await _product(db, sd_id)
    others = (
        await db.execute(
            select(SdProduct.sd_id, SdProduct.name)
            .join(ProductBarcode, ProductBarcode.product_id == SdProduct.id)
            .where(ProductBarcode.barcode == body.barcode, SdProduct.id != product.id)
        )
    ).all()
    try:
        async with db.begin_nested():
            db.add(ProductBarcode(product_id=product.id, barcode=body.barcode, source="admin"))
            await db.flush()
    except IntegrityError as exc:
        raise problem(409, "barcode_exists", message="Barcode already set on this product") from exc
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="product.barcode_add",
        entity_type="product",
        entity_id=sd_id,
        after={"barcode": body.barcode, "also_on": [o[0] for o in others]},
        meta=meta,
    )
    await db.commit()
    out = await _one(db, product)
    out["warnings"] = ["barcode_used_by_other_products"] if others else []
    out["other_products"] = [{"sd_id": s, "name": n} for s, n in others]
    return out


@router.delete("/{sd_id}/barcodes", response_model=ProductOut, response_model_exclude_unset=True)
async def remove_barcode(
    sd_id: SdIdPath,
    ctx: WriteDep,
    db: DbDep,
    meta: MetaDep,
    body: BarcodeIn | None = None,
    barcode: Annotated[str | None, Query(pattern=BARCODE_PATTERN)] = None,
) -> dict[str, Any]:
    code = body.barcode if body else barcode
    if code is None:
        raise HTTPException(422, "barcode is required (JSON body or ?barcode=)")
    product = await _product(db, sd_id)
    row = await db.scalar(
        select(ProductBarcode).where(
            ProductBarcode.product_id == product.id, ProductBarcode.barcode == code
        )
    )
    if row is None:
        raise HTTPException(404, "Barcode not found")
    if row.source != "admin":
        raise problem(409, "barcode_from_sd", message="Barcodes from SD cannot be removed here")
    await db.delete(row)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="product.barcode_remove",
        entity_type="product",
        entity_id=sd_id,
        before={"barcode": code},
        meta=meta,
    )
    await db.commit()
    return await _one(db, product)

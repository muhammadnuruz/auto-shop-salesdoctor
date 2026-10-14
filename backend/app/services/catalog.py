"""Shop catalog queries: only active, un-hidden, link-allowed products with a price > 0."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import ColumnElement, Select, and_, case, func, null, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.link_settings import LinkSettings
from app.domain.order_rules import filter_condition
from app.models import (
    ProductBarcode,
    ProductSetting,
    SdPrice,
    SdPriceType,
    SdProduct,
    SdProductCategory,
    SdStock,
    SdUnit,
    SdWarehouse,
)
from app.services.orders import HeaderOptions, num, problem


@dataclass
class CatalogContext:
    price_type_id: int
    price_type_sd_id: str
    warehouse_id: int | None
    warehouse_sd_id: str | None
    st: LinkSettings | None  # None = no link product filter (admin)


def _pick(label: str, requested: str | None, allowed: list[str]) -> str | None:
    if requested is None:
        return allowed[0] if allowed else None
    if requested not in allowed:
        raise problem(422, "not_allowed", field=label, message=f"{label} is not allowed")
    return requested


async def resolve_context(
    db: AsyncSession,
    options: HeaderOptions,
    st: LinkSettings | None,
    *,
    price_type: str | None,
    warehouse: str | None,
) -> CatalogContext:
    """Validate the requested price type / warehouse against the link allowances (else 422)."""
    pt = _pick("price_type", price_type, options.price_types)
    if pt is None:
        raise problem(422, "no_price_type", message="No price type is available on this link")
    wh = _pick("warehouse", warehouse, options.warehouses)
    pt_id = await db.scalar(select(SdPriceType.id).where(SdPriceType.sd_id == pt))
    wh_id = await db.scalar(select(SdWarehouse.id).where(SdWarehouse.sd_id == wh)) if wh else None
    if pt_id is None:  # pragma: no cover - options come from the same table
        raise HTTPException(422, "Unknown price type")
    return CatalogContext(pt_id, pt, wh_id, wh, st)


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def orderable_conditions(cx: CatalogContext) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [
        SdProduct.active.is_(True),
        func.coalesce(ProductSetting.hidden, False).is_(False),
        SdPrice.price > 0,
    ]
    if cx.st is not None:
        conds.append(filter_condition(cx.st.product_filter))
    return conds


def _base(cx: CatalogContext, *columns: Any) -> Select[Any]:
    return (
        select(*columns)
        .select_from(SdProduct)
        .join(
            SdPrice,
            and_(SdPrice.product_id == SdProduct.id, SdPrice.price_type_id == cx.price_type_id),
        )
        .outerjoin(ProductSetting, ProductSetting.product_id == SdProduct.id)
        .where(*orderable_conditions(cx))
    )


def _exact(q: str) -> ColumnElement[bool]:
    return or_(
        SdProduct.id.in_(select(ProductBarcode.product_id).where(ProductBarcode.barcode == q)),
        SdProduct.code_1c == q,
        SdProduct.sd_id == q,
    )


def _name_match(q: str) -> ColumnElement[bool]:
    tokens = [t for t in q.split() if t][:6]
    all_tokens = and_(*[SdProduct.name.ilike(_like(t)) for t in tokens])
    return or_(all_tokens, SdProduct.name.op("%")(q))


async def search_products(
    db: AsyncSession,
    cx: CatalogContext,
    *,
    q: str | None,
    category: str | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    conds: list[ColumnElement[bool]] = []
    order_by: list[Any] = [SdProduct.name, SdProduct.id]
    if q:
        q = q.strip()
        conds.append(or_(_exact(q), _name_match(q)))
        rank = case(
            (_exact(q), 3),
            (SdProduct.name.ilike(_like(q).replace("%", "", 1)), 2),  # prefix match
            (SdProduct.name.ilike(_like(q)), 1),
            else_=0,
        )
        order_by = [
            rank.desc(),
            func.similarity(SdProduct.name, q).desc(),
            SdProduct.name,
            SdProduct.id,
        ]
    if category:
        conds.append(
            or_(SdProduct.category_sd_id == category, SdProduct.sub_category_sd_id == category)
        )

    total = (await db.scalar(_base(cx, func.count(SdProduct.id)).where(*conds))) or 0

    stmt = _with_names_and_stock(
        cx, _base(cx, SdProduct, SdPrice.price, SdProductCategory.name, SdUnit.name)
    )
    rows = (
        await db.execute(
            stmt.where(*conds).order_by(*order_by).limit(size).offset((page - 1) * size)
        )
    ).all()
    return await _items(db, rows), total


async def _items(db: AsyncSession, rows: list[Any]) -> list[dict[str, Any]]:
    return [item for _, item in await _items_with_id(db, rows)]


async def _items_with_id(db: AsyncSession, rows: list[Any]) -> list[tuple[int, dict[str, Any]]]:
    pids = [r[0].id for r in rows]
    barcodes: dict[int, list[str]] = {}
    if pids:
        for pid, code in (
            await db.execute(
                select(ProductBarcode.product_id, ProductBarcode.barcode)
                .where(ProductBarcode.product_id.in_(pids))
                .order_by(ProductBarcode.id)
            )
        ).all():
            barcodes.setdefault(pid, []).append(code)
    out: list[tuple[int, dict[str, Any]]] = []
    for product, price, cat_name, unit_name, stock in rows:
        out.append(
            (
                product.id,
                {
                    "product_ref": product.sd_id,
                    "code": product.code_1c,
                    "name": product.name,
                    "category": product.category_sd_id,
                    "category_name": cat_name,
                    "image_url": product.image_url,
                    "thumb_url": product.thumb_url,
                    "pack_quantity": num(product.pack_quantity),
                    "unit": unit_name,
                    "price": num(price),
                    "stock": num(stock) if stock is not None else None,
                    "barcodes": barcodes.get(product.id, []),
                },
            )
        )
    return out


async def find_by_barcode(
    db: AsyncSession, cx: CatalogContext, code: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Orderable products carrying this exact barcode (SD has duplicates, so possibly several)."""
    stmt = _with_names_and_stock(
        cx, _base(cx, SdProduct, SdPrice.price, SdProductCategory.name, SdUnit.name)
    )
    stmt = stmt.where(
        SdProduct.id.in_(select(ProductBarcode.product_id).where(ProductBarcode.barcode == code))
    )
    rows = (await db.execute(stmt.order_by(SdProduct.name, SdProduct.id).limit(limit))).all()
    return await _items(db, rows)


async def list_categories(db: AsyncSession, cx: CatalogContext) -> list[dict[str, Any]]:
    """Categories that contain at least one orderable product."""
    rows = (
        await db.execute(
            _base(cx, SdProduct.category_sd_id, SdProductCategory.name, func.count(SdProduct.id))
            .outerjoin(SdProductCategory, SdProductCategory.sd_id == SdProduct.category_sd_id)
            .where(SdProduct.category_sd_id.is_not(None))
            .group_by(SdProduct.category_sd_id, SdProductCategory.name)
            .order_by(SdProductCategory.name, SdProduct.category_sd_id)
        )
    ).all()
    return [{"id": cid, "name": name, "products": count} for cid, name, count in rows]


async def stock_for(
    db: AsyncSession, warehouse_sd_id: str, product_refs: list[str]
) -> dict[str, Decimal]:
    rows = await db.execute(
        select(SdProduct.sd_id, func.coalesce(SdStock.quantity, 0))
        .select_from(SdProduct)
        .outerjoin(
            SdStock,
            and_(
                SdStock.product_id == SdProduct.id,
                SdStock.warehouse_id
                == select(SdWarehouse.id)
                .where(SdWarehouse.sd_id == warehouse_sd_id)
                .scalar_subquery(),
            ),
        )
        .where(SdProduct.sd_id.in_(product_refs))
    )
    return dict(rows.all())


def _with_names_and_stock(cx: CatalogContext, stmt: Select[Any]) -> Select[Any]:
    stmt = stmt.outerjoin(SdProductCategory, SdProductCategory.sd_id == SdProduct.category_sd_id)
    stmt = stmt.outerjoin(SdUnit, SdUnit.sd_id == SdProduct.unit_sd_id)
    if cx.warehouse_id is not None:
        return stmt.add_columns(func.coalesce(SdStock.quantity, 0)).outerjoin(
            SdStock,
            and_(SdStock.product_id == SdProduct.id, SdStock.warehouse_id == cx.warehouse_id),
        )
    return stmt.add_columns(null())


async def orderable_items(
    db: AsyncSession, cx: CatalogContext, product_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Catalog items for the orderable subset of ``product_ids`` (keyed by internal id)."""
    if not product_ids:
        return {}
    stmt = _with_names_and_stock(
        cx, _base(cx, SdProduct, SdPrice.price, SdProductCategory.name, SdUnit.name)
    )
    rows = (await db.execute(stmt.where(SdProduct.id.in_(product_ids)))).all()
    return dict(await _items_with_id(db, rows))


async def context_from_order(
    db: AsyncSession, order: Any, st: LinkSettings | None
) -> CatalogContext:
    """Catalog context for an order's own price type / warehouse (422 if no price type yet)."""
    if order.price_type_sd_id is None:
        raise problem(422, "price_type_required", message="Select a price type first")
    pt_id = await db.scalar(
        select(SdPriceType.id).where(SdPriceType.sd_id == order.price_type_sd_id)
    )
    wh_id = (
        await db.scalar(select(SdWarehouse.id).where(SdWarehouse.sd_id == order.warehouse_sd_id))
        if order.warehouse_sd_id
        else None
    )
    if pt_id is None:
        raise problem(422, "price_type_required", message="Select a price type first")
    return CatalogContext(pt_id, order.price_type_sd_id, wh_id, order.warehouse_sd_id, st)

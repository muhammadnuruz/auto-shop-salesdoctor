"""Shop catalog + stock API (prefix /api/shop). Always scoped by the session's link."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select

from app.api.deps import (
    DbDep,
    RedisDep,
    SdDep,
    SettingsDep,
    ShopContext,
    ShopDep,
    check_origin,
    enforce_rate_limit,
)
from app.api.schemas import BarcodeItemsOut, CatalogOut, CategoriesOut, StockOut
from app.core.ratelimit import Limit
from app.integrations.salesdoctor import SDError
from app.models import SdProduct
from app.services import catalog
from app.services.eligibility import parse_settings
from app.services.orders import link_options, num, problem
from app.services.stock_fresh import apply_to_mirror, fetch_warehouse_stock

router = APIRouter(prefix="/api/shop", tags=["shop-catalog"], dependencies=[Depends(check_origin)])

MAX_STOCK_PRODUCTS = 200


async def shop_catalog_context(
    ctx: ShopContext, db: DbDep, price_type: str | None, warehouse: str | None
) -> catalog.CatalogContext:
    st = parse_settings(ctx.link)
    if st is None:
        raise problem(409, "link_misconfigured", message="Link settings are invalid")
    options = await link_options(db, ctx.client, st)
    return await catalog.resolve_context(
        db, options, st, price_type=price_type, warehouse=warehouse
    )


@router.get("/catalog", response_model=CatalogOut)
async def get_catalog(
    ctx: ShopDep,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    price_type: Annotated[str | None, Query(max_length=64)] = None,
    warehouse: Annotated[str | None, Query(max_length=64)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    cx = await shop_catalog_context(ctx, db, price_type, warehouse)
    items, total = await catalog.search_products(
        db, cx, q=q, category=category, page=page, size=size
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "price_type": cx.price_type_sd_id,
        "warehouse": cx.warehouse_sd_id,
    }


@router.get("/catalog/categories", response_model=CategoriesOut)
async def get_categories(
    ctx: ShopDep,
    db: DbDep,
    price_type: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    cx = await shop_catalog_context(ctx, db, price_type, None)
    return {"items": await catalog.list_categories(db, cx)}


@router.get("/catalog/barcode/{code}", response_model=BarcodeItemsOut)
async def get_by_barcode(
    ctx: ShopDep,
    db: DbDep,
    code: Annotated[str, Path(min_length=1, max_length=64, pattern=r"^[0-9A-Za-z._-]+$")],
    price_type: Annotated[str | None, Query(max_length=64)] = None,
    warehouse: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    cx = await shop_catalog_context(ctx, db, price_type, warehouse)
    return {"items": await catalog.find_by_barcode(db, cx, code)}


@router.get("/stock", response_model=StockOut)
async def get_stock(
    ctx: ShopDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    sd: SdDep,
    warehouse: Annotated[str, Query(max_length=64)],
    products: Annotated[str, Query(min_length=1, max_length=8000)],
    fresh: bool = False,
) -> dict[str, Any]:
    st = parse_settings(ctx.link)
    if st is None:
        raise problem(409, "link_misconfigured", message="Link settings are invalid")
    options = await link_options(db, ctx.client, st)
    if warehouse not in options.warehouses:
        raise problem(422, "not_allowed", field="warehouse", message="warehouse is not allowed")
    refs = list(dict.fromkeys(p.strip() for p in products.split(",") if p.strip()))
    if not refs or len(refs) > MAX_STOCK_PRODUCTS or any(len(r) > 64 for r in refs):
        raise HTTPException(422, f"Provide 1..{MAX_STOCK_PRODUCTS} product refs")
    known = set((await db.scalars(select(SdProduct.sd_id).where(SdProduct.sd_id.in_(refs)))).all())
    refs = [r for r in refs if r in known]
    if fresh:
        await enforce_rate_limit(
            redis,
            [Limit(f"shopfresh:{ctx.client.id}", settings.RL_SHOP_FRESH_STOCK_PER_MIN, 60)],
        )
        try:
            live = await fetch_warehouse_stock(sd, warehouse)
        except SDError as exc:
            raise problem(503, "sd_unavailable", message="Sales Doctor is unavailable") from exc
        if live is not None:
            await apply_to_mirror(
                db, warehouse, {r: live[r] for r in refs if r in live}, force_zero=refs
            )
            await db.commit()
    qty = await catalog.stock_for(db, warehouse, refs)
    return {
        "warehouse": warehouse,
        "fresh": bool(fresh),
        "items": [{"product_ref": r, "quantity": num(qty.get(r, 0))} for r in refs],
    }

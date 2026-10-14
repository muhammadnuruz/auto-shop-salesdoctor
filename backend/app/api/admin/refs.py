"""Reference lists for admin forms (links.read | orders.read | orders.write), from the mirror."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import AdminContext, DbDep, require_any
from app.models import (
    SdAgent,
    SdBrand,
    SdClient,
    SdPriceType,
    SdProductCategory,
    SdTradeDirection,
    SdWarehouse,
)

router = APIRouter(prefix="/refs", tags=["admin-refs"])
ReadDep = Annotated[AdminContext, Depends(require_any("links.read", "orders.read", "orders.write"))]


class RefItem(BaseModel):
    id: str
    name: str | None


class ClientIdCount(BaseModel):
    id: str
    clients: int


class RefsOut(BaseModel):
    warehouses: list[RefItem]
    price_types: list[RefItem]
    agents: list[RefItem]
    trade_directions: list[RefItem]
    categories: list[RefItem]
    brands: list[RefItem]
    territories: list[ClientIdCount]
    client_categories: list[ClientIdCount]


@router.get("", response_model=RefsOut)
async def refs(ctx: ReadDep, db: DbDep) -> dict[str, Any]:
    """Active warehouses, sale price types (never purchase), agents, trade directions,
    categories, brands, plus territories (client city ids) and client categories (ids with counts;
    SD names for those two are not mirrored)."""

    async def named(model: Any, *where: Any) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(model.sd_id, model.name)
                .where(model.active.is_(True), *where)
                .order_by(model.name, model.sd_id)
            )
        ).all()
        return [{"id": sd_id, "name": name} for sd_id, name in rows]

    async def client_ids(column: Any) -> list[dict[str, Any]]:
        rows = (
            await db.execute(
                select(column, func.count())
                .where(SdClient.active.is_(True), column.is_not(None))
                .group_by(column)
                .order_by(column)
            )
        ).all()
        return [{"id": cid, "clients": count} for cid, count in rows]

    return {
        "warehouses": await named(SdWarehouse),
        "price_types": await named(SdPriceType, SdPriceType.kind == "sale"),
        "agents": await named(SdAgent),
        "trade_directions": await named(SdTradeDirection),
        "categories": await named(SdProductCategory),
        "brands": await named(SdBrand),
        "territories": await client_ids(SdClient.city_sd_id),
        "client_categories": await client_ids(SdClient.category_sd_id),
    }

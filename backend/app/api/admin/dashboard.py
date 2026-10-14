"""Admin dashboard: order KPIs, breakdowns and sync freshness (reports.read)."""

from datetime import datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on

from app.api.deps import AdminContext, DbDep, require
from app.api.schemas import Number
from app.core import clock
from app.models import AccessLink, SdClient, SdProduct, ShopOrder, ShopOrderLine, SyncRun
from app.services.orders import num
from app.sync.common import SD_TZ

router = APIRouter(prefix="/dashboard", tags=["admin-dashboard"])
ReadDep = Annotated[AdminContext, Depends(require("reports.read"))]

# orders that represent real demand (drafts / failed / cancelled / deleted are excluded)
PLACED = ("sent", "submitting", "outcome_unknown")
TOP_N = 10


class OrdersSum(BaseModel):
    orders: int
    sum: Number


class TopClient(OrdersSum):
    client_id: int
    name: str | None


class TopProduct(BaseModel):
    product_ref: str
    name: str | None
    qty: Number | None
    sum: Number


class ByLink(OrdersSum):
    link_id: int
    slug: str
    name: str


class SyncRunOut(BaseModel):
    status: str
    started_at: datetime
    finished_at: datetime | None
    items: int | None
    error: str | None


class DashboardOut(BaseModel):
    periods: dict[str, OrdersSum]  # keys: today, 7d, 30d
    by_state: dict[str, OrdersSum]  # keyed by order state (last 30 days)
    top_clients: list[TopClient]
    top_products: list[TopProduct]
    by_link: list[ByLink]
    failed: int
    outcome_unknown: int
    reconcile_exhausted: int
    last_sync: dict[str, SyncRunOut]  # keyed by sync entity


@router.get("", response_model=DashboardOut)
async def dashboard(ctx: ReadDep, db: DbDep) -> dict[str, Any]:
    now = clock.utcnow()
    midnight = datetime.combine(now.astimezone(SD_TZ).date(), time.min, SD_TZ)
    starts = {
        "today": midnight,
        "7d": midnight - timedelta(days=6),
        "30d": midnight - timedelta(days=29),
    }
    since = starts["30d"]

    periods: dict[str, Any] = {}
    for name, start in starts.items():
        count, total = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(ShopOrder.total_sum), 0)).where(
                    ShopOrder.state.in_(PLACED), ShopOrder.created_at >= start
                )
            )
        ).one()
        periods[name] = {"orders": count, "sum": num(total)}

    by_state = {
        state: {"orders": count, "sum": num(total)}
        for state, count, total in (
            await db.execute(
                select(
                    ShopOrder.state, func.count(), func.coalesce(func.sum(ShopOrder.total_sum), 0)
                )
                .where(ShopOrder.created_at >= since)
                .group_by(ShopOrder.state)
            )
        ).all()
    }
    top_clients = [
        {"client_id": cid, "name": name, "orders": count, "sum": num(total)}
        for cid, name, count, total in (
            await db.execute(
                select(
                    SdClient.id,
                    SdClient.name,
                    func.count(),
                    func.coalesce(func.sum(ShopOrder.total_sum), 0).label("s"),
                )
                .join(ShopOrder, ShopOrder.client_id == SdClient.id)
                .where(ShopOrder.state.in_(PLACED), ShopOrder.created_at >= since)
                .group_by(SdClient.id, SdClient.name)
                .order_by(func.sum(ShopOrder.total_sum).desc(), SdClient.id)
                .limit(TOP_N)
            )
        ).all()
    ]
    top_products = [
        {"product_ref": ref, "name": name, "qty": num(qty), "sum": num(total)}
        for ref, name, qty, total in (
            await db.execute(
                select(
                    SdProduct.sd_id,
                    SdProduct.name,
                    func.sum(ShopOrderLine.quantity),
                    func.coalesce(func.sum(ShopOrderLine.line_sum), 0),
                )
                .join(ShopOrderLine, ShopOrderLine.product_id == SdProduct.id)
                .join(ShopOrder, ShopOrder.id == ShopOrderLine.order_id)
                .where(ShopOrder.state.in_(PLACED), ShopOrder.created_at >= since)
                .group_by(SdProduct.sd_id, SdProduct.name)
                .order_by(func.sum(ShopOrderLine.quantity).desc(), SdProduct.sd_id)
                .limit(TOP_N)
            )
        ).all()
    ]
    by_link = [
        {"link_id": lid, "slug": slug, "name": name, "orders": count, "sum": num(total)}
        for lid, slug, name, count, total in (
            await db.execute(
                select(
                    AccessLink.id,
                    AccessLink.slug,
                    AccessLink.name,
                    func.count(),
                    func.coalesce(func.sum(ShopOrder.total_sum), 0),
                )
                .join(ShopOrder, ShopOrder.link_id == AccessLink.id)
                .where(ShopOrder.state.in_(PLACED), ShopOrder.created_at >= since)
                .group_by(AccessLink.id, AccessLink.slug, AccessLink.name)
                .order_by(func.sum(ShopOrder.total_sum).desc(), AccessLink.id)
            )
        ).all()
    ]
    problem_counts = dict(
        (
            await db.execute(
                select(ShopOrder.state, func.count())
                .where(ShopOrder.state.in_(("failed", "outcome_unknown")))
                .group_by(ShopOrder.state)
            )
        ).all()
    )
    exhausted = (
        await db.scalar(
            select(func.count())
            .select_from(ShopOrder)
            .where(
                ShopOrder.state == "outcome_unknown",
                ShopOrder.last_error["exhausted"].as_boolean().is_(True),
            )
        )
    ) or 0
    runs = (
        await db.scalars(
            select(SyncRun)
            .ext(distinct_on(SyncRun.entity))
            .order_by(SyncRun.entity, SyncRun.started_at.desc())
        )
    ).all()
    return {
        "periods": periods,
        "by_state": by_state,
        "top_clients": top_clients,
        "top_products": top_products,
        "by_link": by_link,
        "failed": problem_counts.get("failed", 0),
        "outcome_unknown": problem_counts.get("outcome_unknown", 0),
        "reconcile_exhausted": exhausted,  # parked orders: SD state unverified, need an operator
        "last_sync": {
            r.entity: {
                "status": r.status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "items": r.items,
                "error": r.error,
            }
            for r in runs
        },
    }

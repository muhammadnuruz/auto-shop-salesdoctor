"""Sales reports (reports.read): totals grouped by client / product / category / link / agent /
day over a period, as JSON or an .xlsx download.

Only placed orders count (same definition as the dashboard). An order belongs to the day it was
submitted (falls back to creation time), in the dealer's time zone (Asia/Tashkent).
"""

import io
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import ColumnElement, String, cast, func, literal_column, select

from app.api.admin.dashboard import PLACED
from app.api.deps import AdminContext, DbDep, MetaDep, require
from app.api.schemas import Number
from app.models import (
    AccessLink,
    SdAgent,
    SdClient,
    SdProduct,
    SdProductCategory,
    ShopOrder,
    ShopOrderLine,
)
from app.services import audit
from app.services.orders import num
from app.sync.common import SD_TZ

router = APIRouter(prefix="/reports", tags=["admin-reports"])
ReadDep = Annotated[AdminContext, Depends(require("reports.read"))]

GroupBy = Literal["client", "product", "category", "link", "agent", "day"]
MAX_DAYS = 366
MAX_ROWS = 5000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ReportRow(BaseModel):
    key: str | None
    name: str | None
    orders: int
    qty: Number | None
    sum: Number


class ReportTotals(BaseModel):
    orders: int
    qty: Number | None
    sum: Number


class SalesReport(BaseModel):
    group_by: GroupBy
    date_from: date
    date_to: date
    totals: ReportTotals
    rows: list[ReportRow]
    truncated: bool


class Params:
    def __init__(
        self,
        group_by: GroupBy = "client",
        date_from: date | None = None,
        date_to: date | None = None,
        link_id: Annotated[int | None, Query(ge=1)] = None,
        client_id: Annotated[int | None, Query(ge=1)] = None,
        agent: Annotated[str | None, Query(max_length=64)] = None,
    ) -> None:
        today = datetime.now(SD_TZ).date()
        self.date_to = date_to or today
        self.date_from = date_from or (self.date_to - timedelta(days=29))
        if self.date_from > self.date_to:
            raise HTTPException(422, "date_from must not be after date_to")
        if (self.date_to - self.date_from).days >= MAX_DAYS:
            raise HTTPException(422, f"period must not exceed {MAX_DAYS} days")
        self.group_by = group_by
        self.link_id = link_id
        self.client_id = client_id
        self.agent = agent

    @property
    def start(self) -> datetime:
        return datetime.combine(self.date_from, time.min, SD_TZ)

    @property
    def end(self) -> datetime:  # exclusive
        return datetime.combine(self.date_to + timedelta(days=1), time.min, SD_TZ)


def _order_time() -> ColumnElement[datetime]:
    return func.coalesce(ShopOrder.submitted_at, ShopOrder.created_at)


def _conds(p: Params) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [
        ShopOrder.state.in_(PLACED),
        _order_time() >= p.start,
        _order_time() < p.end,
    ]
    if p.link_id is not None:
        conds.append(ShopOrder.link_id == p.link_id)
    if p.client_id is not None:
        conds.append(ShopOrder.client_id == p.client_id)
    if p.agent:
        conds.append(ShopOrder.agent_sd_id == p.agent)
    return conds


async def build_report(db: DbDep, p: Params) -> dict[str, Any]:
    conds = _conds(p)
    orders = func.count(func.distinct(ShopOrder.id))
    order_level = p.group_by in ("client", "link", "agent", "day")
    qty_col = func.sum(ShopOrder.total_qty) if order_level else func.sum(ShopOrderLine.quantity)
    sum_col = (
        func.coalesce(func.sum(ShopOrder.total_sum), 0)
        if order_level
        else func.coalesce(func.sum(ShopOrderLine.line_sum), 0)
    )
    q: Any
    if p.group_by == "client":
        q = (
            select(cast(SdClient.id, String), SdClient.name, orders, qty_col, sum_col)
            .join(SdClient, SdClient.id == ShopOrder.client_id)
            .group_by(SdClient.id, SdClient.name)
        )
    elif p.group_by == "link":
        q = (
            select(cast(AccessLink.id, String), AccessLink.name, orders, qty_col, sum_col)
            .join(AccessLink, AccessLink.id == ShopOrder.link_id)
            .group_by(AccessLink.id, AccessLink.name)
        )
    elif p.group_by == "agent":
        q = (
            select(ShopOrder.agent_sd_id, SdAgent.name, orders, qty_col, sum_col)
            .outerjoin(SdAgent, SdAgent.sd_id == ShopOrder.agent_sd_id)
            .group_by(ShopOrder.agent_sd_id, SdAgent.name)
        )
    elif p.group_by == "day":
        # constants inlined (not bound) so the SELECT and GROUP BY expressions are identical
        day = func.to_char(
            func.timezone(literal_column(f"'{SD_TZ.key}'"), _order_time()),
            literal_column("'YYYY-MM-DD'"),
        )
        q = select(day, day, orders, qty_col, sum_col).group_by(day)
    elif p.group_by == "product":
        q = (
            select(SdProduct.sd_id, SdProduct.name, orders, qty_col, sum_col)
            .join(ShopOrderLine, ShopOrderLine.order_id == ShopOrder.id)
            .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
            .group_by(SdProduct.sd_id, SdProduct.name)
        )
    else:  # category
        q = (
            select(SdProduct.category_sd_id, SdProductCategory.name, orders, qty_col, sum_col)
            .join(ShopOrderLine, ShopOrderLine.order_id == ShopOrder.id)
            .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
            .outerjoin(SdProductCategory, SdProductCategory.sd_id == SdProduct.category_sd_id)
            .group_by(SdProduct.category_sd_id, SdProductCategory.name)
        )
    q = q.select_from(ShopOrder).where(*conds)
    if p.group_by == "day":
        q = q.order_by(literal_column("1"))
    else:
        q = q.order_by(sum_col.desc(), literal_column("1"))
    rows = (await db.execute(q.limit(MAX_ROWS + 1))).all()
    truncated = len(rows) > MAX_ROWS

    count, total_qty, total_sum = (
        await db.execute(
            select(
                func.count(),
                func.sum(ShopOrder.total_qty),
                func.coalesce(func.sum(ShopOrder.total_sum), 0),
            ).where(*conds)
        )
    ).one()
    return {
        "group_by": p.group_by,
        "date_from": p.date_from,
        "date_to": p.date_to,
        "totals": {"orders": count, "qty": num(total_qty), "sum": num(total_sum)},
        "rows": [
            {"key": key, "name": name, "orders": n, "qty": num(qty), "sum": num(s)}
            for key, name, n, qty, s in rows[:MAX_ROWS]
        ],
        "truncated": truncated,
    }


@router.get("/sales", response_model=SalesReport)
async def sales_report(ctx: ReadDep, db: DbDep, p: Annotated[Params, Depends()]) -> dict[str, Any]:
    return await build_report(db, p)


def _safe(value: Any) -> Any:
    # neutralise spreadsheet formula injection from SD/user-controlled text
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@router.get("/sales.xlsx")
async def sales_report_xlsx(
    ctx: ReadDep, db: DbDep, meta: MetaDep, p: Annotated[Params, Depends()]
) -> Response:
    from openpyxl import Workbook

    report = await build_report(db, p)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(f"by_{p.group_by}")
    ws.append([f"Period: {p.date_from.isoformat()} — {p.date_to.isoformat()}"])
    ws.append(["Key", "Name", "Orders", "Qty", "Sum"])
    for r in report["rows"]:
        ws.append([_safe(r["key"]), _safe(r["name"]), r["orders"], r["qty"], r["sum"]])
    t = report["totals"]
    ws.append(["", "TOTAL", t["orders"], t["qty"], t["sum"]])
    buf = io.BytesIO()
    wb.save(buf)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="report.export",
        entity_type="report",
        entity_id=f"sales:{p.group_by}",
        after={
            "date_from": p.date_from,
            "date_to": p.date_to,
            "rows": len(report["rows"]),
        },
        meta=meta,
    )
    await db.commit()
    name = f"sales_{p.group_by}_{p.date_from.isoformat()}_{p.date_to.isoformat()}.xlsx"
    return Response(
        buf.getvalue(),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )

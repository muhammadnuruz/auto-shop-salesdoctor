"""Fresh stock straight from SD for one warehouse, plus mirror write-back."""

from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.integrations.salesdoctor import SalesDoctorClient
from app.models import SdProduct, SdStock, SdWarehouse
from app.sync.common import bulk_upsert, chunks, dec, utcnow

log = get_logger(__name__)


async def fetch_warehouse_stock(
    sd: SalesDoctorClient, warehouse_sd_id: str
) -> dict[str, Decimal] | None:
    """{product SD_id: available quantity} for one warehouse; None if SD did not list it.

    Raises ``SDError`` subclasses on transport/protocol failures (callers decide the fallback).
    """
    for wh in await sd.get_stock():
        if wh.SD_id == warehouse_sd_id:
            out: dict[str, Decimal] = {}
            for p in wh.products:
                qty = dec(p.quantity)
                if p.SD_id and qty is not None:
                    out[p.SD_id] = qty
            return out
    log.warning("fresh_stock_warehouse_missing", warehouse=warehouse_sd_id)
    return None


async def apply_to_mirror(
    db: AsyncSession,
    warehouse_sd_id: str,
    quantities: dict[str, Decimal],
    *,
    zero_missing: bool = False,
    force_zero: Iterable[str] = (),
) -> int:
    """Upsert ``quantities`` into sd_stock for one warehouse.

    ``force_zero`` product SD ids are written as 0 when absent from ``quantities`` (SD omits
    out-of-stock products). ``zero_missing`` zeroes every other non-zero row of the warehouse.
    Does not commit.
    """
    wh_id = await db.scalar(select(SdWarehouse.id).where(SdWarehouse.sd_id == warehouse_sd_id))
    if wh_id is None:
        return 0
    wanted = dict(quantities)
    for sd_id in force_zero:
        wanted.setdefault(sd_id, Decimal(0))
    if not wanted and not zero_missing:
        return 0
    ids = dict(
        (
            await db.execute(
                select(SdProduct.sd_id, SdProduct.id).where(SdProduct.sd_id.in_(list(wanted)))
            )
        ).all()
    )
    now = utcnow()
    rows = [
        {"warehouse_id": wh_id, "product_id": ids[sd_id], "quantity": qty, "synced_at": now}
        for sd_id, qty in wanted.items()
        if sd_id in ids
    ]
    await bulk_upsert(db, SdStock, rows, conflict=["warehouse_id", "product_id"])
    if zero_missing:
        seen = {r["product_id"] for r in rows}
        nonzero = (
            await db.scalars(
                select(SdStock.product_id).where(
                    SdStock.warehouse_id == wh_id, SdStock.quantity != 0
                )
            )
        ).all()
        gone = [p for p in nonzero if p not in seen]
        for batch in chunks(gone):
            await db.execute(
                update(SdStock)
                .where(SdStock.warehouse_id == wh_id, SdStock.product_id.in_(list(batch)))
                .values(quantity=0, synced_at=now)
                .execution_options(synchronize_session=False)
            )
    return len(rows)

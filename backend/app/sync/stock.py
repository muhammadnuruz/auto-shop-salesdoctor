from __future__ import annotations

from typing import Any

from sqlalchemy import select, tuple_, update

from app.models import SdProduct, SdStock, SdWarehouse
from app.sync.common import SyncOutcome, bulk_upsert, chunks, dec, utcnow
from app.sync.context import SyncContext


async def sync_stock(ctx: SyncContext) -> SyncOutcome:
    out = SyncOutcome()
    payload = await ctx.sd.get_stock()  # fetched before any DB transaction
    now = utcnow()
    async with ctx.sessionmaker() as session:
        warehouses = dict((await session.execute(select(SdWarehouse.sd_id, SdWarehouse.id))).all())
        products = dict((await session.execute(select(SdProduct.sd_id, SdProduct.id))).all())

        rows: list[dict[str, Any]] = []
        seen_warehouses: set[int] = set()
        for wh in payload:
            wh_id = warehouses.get(wh.SD_id or "")
            if wh_id is None:
                out.warn("unknown_warehouse")
                continue
            seen_warehouses.add(wh_id)
            for p in wh.products:
                product_id = products.get(p.SD_id or "")
                if product_id is None:
                    out.warn("unknown_product")
                    continue
                qty = dec(p.quantity)
                if qty is None and p.quantity is not None:
                    out.warn("bad_quantity")
                    continue
                rows.append(
                    {
                        "warehouse_id": wh_id,
                        "product_id": product_id,
                        "quantity": qty if qty is not None else 0,
                        "synced_at": now,
                    }
                )
        if not rows:
            out.warn("empty_response")  # never zero everything on an empty payload
            return out

        await bulk_upsert(session, SdStock, rows, conflict=["warehouse_id", "product_id"])
        # Products absent from a warehouse that DID appear in the payload are out of stock.
        seen = {(r["warehouse_id"], r["product_id"]) for r in rows}
        nonzero = (
            await session.execute(
                select(SdStock.warehouse_id, SdStock.product_id).where(
                    SdStock.warehouse_id.in_(seen_warehouses), SdStock.quantity != 0
                )
            )
        ).all()
        gone = [(w, p) for w, p in nonzero if (w, p) not in seen]
        for batch in chunks(gone):
            await session.execute(
                update(SdStock)
                .where(tuple_(SdStock.warehouse_id, SdStock.product_id).in_(list(batch)))
                .values(quantity=0, synced_at=now)
                .execution_options(synchronize_session=False)
            )
        out.detail["zeroed"] = len(gone)
        out.items = len(rows)
        await session.commit()
    return out

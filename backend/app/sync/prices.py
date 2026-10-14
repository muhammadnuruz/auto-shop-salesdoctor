from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select

from app.models import SdPrice, SdPriceType, SdProduct
from app.sync.common import SyncOutcome, bulk_upsert, chunks, dec, utcnow
from app.sync.context import SyncContext

MIN_KEEP_RATIO = 0.5  # fewer rows than this share of the current count => do not delete stale


async def sync_prices(ctx: SyncContext) -> SyncOutcome:
    """Prices for ACTIVE price types of kind 'sale' only. Purchase/cost prices are never stored."""
    out = SyncOutcome()
    async with ctx.sessionmaker() as session:
        sellable = (
            await session.execute(
                select(SdPriceType.id, SdPriceType.sd_id).where(
                    SdPriceType.kind == "sale", SdPriceType.active.is_(True)
                )
            )
        ).all()
        products = dict((await session.execute(select(SdProduct.sd_id, SdProduct.id))).all())
    if not sellable:
        out.warn("no_sellable_price_types")  # never delete everything because of this
        return out

    # fetch all price lists first, outside any DB transaction
    fetched: list[tuple[int, str, list[tuple[str, Any]]]] = []
    for pt_id, pt_sd_id in sellable:
        fetched.append((pt_id, pt_sd_id, await ctx.sd.get_prices(pt_sd_id)))

    now = utcnow()
    per_type: dict[str, int] = {}
    async with ctx.sessionmaker() as session:
        # types that became inactive / non-sale (incl. purchase) must not keep prices
        await session.execute(
            delete(SdPrice).where(SdPrice.price_type_id.not_in([t[0] for t in sellable]))
        )
        for pt_id, pt_sd_id, pairs in fetched:
            rows: list[dict[str, Any]] = []
            for product_sd_id, price in pairs:
                product_id = products.get(product_sd_id)
                if price is None:
                    continue
                value = dec(price)
                if product_id is None:
                    out.warn("unknown_product")
                    continue
                if value is None:
                    out.warn("bad_price")
                    continue
                rows.append(
                    {
                        "price_type_id": pt_id,
                        "product_id": product_id,
                        "price": value,
                        "synced_at": now,
                    }
                )
            current = set(
                (
                    await session.scalars(
                        select(SdPrice.product_id).where(SdPrice.price_type_id == pt_id)
                    )
                ).all()
            )
            await bulk_upsert(session, SdPrice, rows, conflict=["price_type_id", "product_id"])

            seen = {r["product_id"] for r in rows}
            stale = sorted(current - seen)
            if stale and (not rows or len(rows) < MIN_KEEP_RATIO * len(current)):
                out.warn("price_cleanup_skipped")
                out.detail.setdefault("price_cleanup_skipped_types", []).append(pt_sd_id)
            else:
                for batch in chunks(stale):
                    await session.execute(
                        delete(SdPrice).where(
                            SdPrice.price_type_id == pt_id, SdPrice.product_id.in_(batch)
                        )
                    )
            per_type[pt_sd_id] = len(rows)
            out.items += len(rows)
        await session.commit()
    out.detail["per_price_type"] = per_type
    return out

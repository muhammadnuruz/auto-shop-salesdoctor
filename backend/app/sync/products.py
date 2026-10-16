from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select

from app.models import ProductBarcode, SdProduct
from app.sync.common import (
    INT32_MAX,
    SyncOutcome,
    bulk_upsert,
    chunks,
    deactivate_missing,
    dec,
    mirror_row,
    ref_id,
    text_or_none,
    utcnow,
)
from app.sync.context import SyncContext


def sd_origin(base_url: str) -> str:
    """``https://host`` of the SD API base URL (media paths are served from the same host)."""
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


def media_url(value: Any, origin: str | None) -> str | None:
    """SD returns image paths relative to its own host (``/upload/adtProduct/x.jpeg``); the SPA
    runs on another host, so make them absolute. Absolute http(s) URLs are kept; anything else
    (protocol-relative, other schemes, bare names) is dropped rather than rendered."""
    s = text_or_none(value)
    if s is None:
        return None
    if s.startswith(("https://", "http://")):
        return s
    if origin and s.startswith("/") and not s.startswith("//"):
        return origin + s
    return None


def product_row(
    raw: dict[str, Any],
    now: datetime,
    out: SyncOutcome | None = None,
    media_origin: str | None = None,
) -> dict[str, Any] | None:
    row = mirror_row(raw, now, out)
    if row is None:
        return None
    sort = raw.get("sort")
    row.update(
        sort=int(sort) if isinstance(sort, int | float) and abs(sort) <= INT32_MAX else None,
        pack_quantity=dec(raw.get("packQuantity")),
        volume=dec(raw.get("volume")),
        weight=dec(raw.get("weight")),
        image_id=text_or_none(raw.get("imageID")),
        image_url=media_url(raw.get("imageUrl"), media_origin),
        thumb_url=media_url(raw.get("thumbUrl"), media_origin),
        category_sd_id=ref_id(raw.get("category")),
        sub_category_sd_id=ref_id(raw.get("subCategory")),
        unit_sd_id=ref_id(raw.get("unit")),
        brand_sd_id=ref_id(raw.get("brand")),
        trade_sd_id=ref_id(raw.get("trade")),
        barcode=text_or_none(raw.get("barCode")),
    )
    return row


async def sync_products(ctx: SyncContext) -> SyncOutcome:
    out = SyncOutcome()
    now = utcnow()
    rows: list[dict[str, Any]] = []
    origin = sd_origin(ctx.settings.SD_BASE_URL)
    # complete-or-raise: iterate_raw raises SDIncompleteFetchError on partial data
    async for raw in ctx.sd.iterate_raw("getProduct", "product"):
        try:
            row = product_row(raw, now, out, origin)
        except (ValueError, TypeError, ArithmeticError):
            out.warn("bad_product_row")
            continue
        if row is None:
            out.warn("product_without_sd_id")
        else:
            rows.append(row)
    if not rows:
        out.warn("empty_response")
        return out

    async with ctx.sessionmaker() as session:
        await bulk_upsert(session, SdProduct, rows, conflict=["sd_id"])
        out.detail["deactivated"] = await deactivate_missing(
            session,
            SdProduct,
            {r["sd_id"] for r in rows},
            out,
            "product",
            ctx.settings.SYNC_MAX_DEACTIVATE_RATIO,
        )
        out.items = len(rows)

        # SD-sourced barcodes are diffed (admin-added ones are never touched).
        ids = dict((await session.execute(select(SdProduct.sd_id, SdProduct.id))).all())
        wanted = {(ids[r["sd_id"]], r["barcode"]) for r in rows if r["barcode"]}
        existing = {
            (pid, code): row_id
            for row_id, pid, code in (
                await session.execute(
                    select(
                        ProductBarcode.id, ProductBarcode.product_id, ProductBarcode.barcode
                    ).where(ProductBarcode.source == "sd")
                )
            ).all()
        }
        stale = [row_id for key, row_id in existing.items() if key not in wanted]
        for batch in chunks(stale):
            await session.execute(delete(ProductBarcode).where(ProductBarcode.id.in_(batch)))
        new = [
            {"product_id": pid, "barcode": code, "source": "sd"}
            for pid, code in wanted
            if (pid, code) not in existing
        ]
        await bulk_upsert(
            session, ProductBarcode, new, conflict=["barcode", "product_id"], do_nothing=True
        )
        out.detail["barcodes"] = len(wanted)
        await session.commit()
    return out

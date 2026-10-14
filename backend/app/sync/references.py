"""Reference tables: warehouses, price types, trade directions, payment types, agents, ..."""

from __future__ import annotations

from typing import Any

from app.models import (
    SdAgent,
    SdBrand,
    SdPaymentType,
    SdPriceType,
    SdProductCategory,
    SdTradeDirection,
    SdUnit,
    SdWarehouse,
)
from app.sync.common import (
    SyncOutcome,
    bulk_upsert,
    deactivate_missing,
    mirror_row,
    ref_id,
    text_or_none,
    utcnow,
)
from app.sync.context import SyncContext


def _price_type_extra(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": text_or_none(raw.get("type")),
        "payment_type_sd_id": ref_id(raw.get("paymentType")),
    }


# (label, model, SD method, result keys, extra-columns fn)
REFERENCES: list[tuple[str, Any, str, tuple[str, ...], Any]] = [
    ("warehouse", SdWarehouse, "getWarehouse", ("warehouse",), None),
    ("price_type", SdPriceType, "getPriceType", ("priceType",), _price_type_extra),
    ("trade_direction", SdTradeDirection, "getTradeDirection", ("tradeDirection",), None),
    ("payment_type", SdPaymentType, "getPaymentType", ("currency", "paymentType"), None),
    ("agent", SdAgent, "getAgent", ("agent",), None),
    ("unit", SdUnit, "getUnit", ("unit",), None),
    ("product_category", SdProductCategory, "getProductCategory", ("productCategory",), None),
    ("brand", SdBrand, "getBrand", ("brand",), None),
]


async def sync_references(ctx: SyncContext) -> SyncOutcome:
    out = SyncOutcome()
    # 1) fetch everything first (no DB transaction open while talking to SD)
    fetched: list[tuple[str, Any, Any, list[dict[str, Any]]]] = []
    for label, model, method, keys, extra in REFERENCES:
        fetched.append((label, model, extra, await ctx.sd.get_raw_list(method, keys)))

    # 2) one short write transaction
    now = utcnow()
    ratio = ctx.settings.SYNC_MAX_DEACTIVATE_RATIO
    async with ctx.sessionmaker() as session:
        for label, model, extra, raw_rows in fetched:
            rows: list[dict[str, Any]] = []
            for raw in raw_rows:
                row = mirror_row(raw, now, out)
                if row is None:
                    out.warn(f"{label}_without_sd_id")
                    continue
                if extra:
                    row.update(extra(raw))
                rows.append(row)
            await bulk_upsert(session, model, rows, conflict=["sd_id"])
            deactivated = 0
            if rows:
                deactivated = await deactivate_missing(
                    session, model, {r["sd_id"] for r in rows}, out, label, ratio
                )
            elif label != "brand":  # brands may legitimately be empty
                out.warn(f"{label}_empty_response")
            out.items += len(rows)
            out.detail[label] = {"rows": len(rows), "deactivated": deactivated}
        await session.commit()
    return out

"""Waybill (накладная) for a sent order.

The data comes from Sales Doctor itself (the order as SD booked it: its number, prices,
quantities, discounts), enriched with names/photos from the mirror. When SD cannot be read
(down, slow, order not found) the waybill falls back to our local copy and says so.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.integrations.salesdoctor import SalesDoctorClient, SDError
from app.integrations.salesdoctor.schemas import Order
from app.models import (
    SdAgent,
    SdClient,
    SdPriceType,
    SdProduct,
    SdTradeDirection,
    SdUnit,
    SdWarehouse,
    ShopOrder,
    ShopOrderLine,
)
from app.services.orders import num, problem
from app.sync.common import SD_TZ

log = structlog.get_logger(__name__)

SD_DT = "%Y-%m-%d %H:%M:%S"
CACHE_TTL = 120  # seconds: print + PDF right after opening reuse one SD read


def _cache_key(order: ShopOrder) -> str:
    return f"invoice:{order.id}:{order.version}"


def order_number(sd_id: str | None, fallback: str) -> str:
    """SD shows order ``d0_30676`` as number 30676."""
    if not sd_id:
        return fallback
    tail = sd_id.rsplit("_", 1)[-1]
    return tail if tail.isdigit() else sd_id


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return Decimal(0)


def _whole_blocks(qty: Decimal, pack: Any, sd_block: Any) -> int:
    """Whole blocks in ``qty`` pieces. SD's own ``block`` is fractional (2 of 10 = 0.2), which
    reads badly on paper, so it is used only when the pack size is unknown and it is whole."""
    size = _dec(pack)
    if size > 0:
        return int(qty // size)
    block = _dec(sd_block)
    return int(block) if block == block.to_integral_value() else 0


def _ref(raw: Any) -> str | None:
    if isinstance(raw, dict):
        value = raw.get("SD_id")
        return None if value in (None, "") else str(value)
    return None


async def _names(db: AsyncSession, model: Any, ids: set[str | None]) -> dict[str, str | None]:
    clean = [i for i in ids if i]
    if not clean:
        return {}
    rows = await db.execute(select(model.sd_id, model.name).where(model.sd_id.in_(clean)))
    return {str(a): b for a, b in rows.all()}


async def find_sd_order(sd: SalesDoctorClient, order: ShopOrder) -> Order | None:
    """The SD order behind ``order``: SD stamps it when we send it, so a ±1 day window around
    the send time is enough (no matter how old the order is)."""
    stamp = (order.sent_at or order.submitted_at or order.created_at or clock.utcnow()).astimezone(
        SD_TZ
    )
    date_from = (stamp - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    date_to = (stamp + timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=0)
    async for o in sd.iterate_orders(
        date_from=date_from.strftime(SD_DT), date_to=date_to.strftime(SD_DT)
    ):
        if (order.sd_id and str(o.SD_id) == order.sd_id) or o.code_1C == order.code_1c:
            return o
    return None


async def _products(db: AsyncSession, refs: set[str]) -> dict[str, dict[str, Any]]:
    if not refs:
        return {}
    rows = await db.execute(
        select(SdProduct, SdUnit.name)
        .outerjoin(SdUnit, SdUnit.sd_id == SdProduct.unit_sd_id)
        .where(SdProduct.sd_id.in_(refs))
    )
    return {
        p.sd_id: {"name": p.name, "thumb_url": p.thumb_url, "unit": unit, "pack": p.pack_quantity}
        for p, unit in rows.all()
    }


async def _header_names(
    db: AsyncSession, agent: str | None, warehouse: str | None, price: str | None, trade: str | None
) -> dict[str, str | None]:
    return {
        "agent": (await _names(db, SdAgent, {agent})).get(agent or ""),
        "warehouse": (await _names(db, SdWarehouse, {warehouse})).get(warehouse or ""),
        "price_type": (await _names(db, SdPriceType, {price})).get(price or ""),
        "trade": (await _names(db, SdTradeDirection, {trade})).get(trade or ""),
    }


def _client_out(client: SdClient | None, sd_name: Any = None) -> dict[str, str | None]:
    return {
        "name": (str(sd_name) if sd_name else None) or (client.name if client else None),
        "phone": client.tel_raw if client else None,
        "address": client.address if client else None,
    }


def _local_time(dt: datetime | None) -> str | None:
    return dt.astimezone(SD_TZ).strftime(SD_DT) if dt else None


async def _from_sd(db: AsyncSession, order: ShopOrder, sd_order: Order) -> dict[str, Any]:
    extra = sd_order.model_extra or {}
    raw_lines = sd_order.orderProducts
    refs = {r for r in (_ref(row.get("product")) for row in raw_lines) if r}
    products = await _products(db, refs)
    lines: list[dict[str, Any]] = []
    for i, row in enumerate(raw_lines, start=1):
        ref = _ref(row.get("product")) or ""
        mirror = products.get(ref, {})
        product = row.get("product") if isinstance(row.get("product"), dict) else {}
        qty, price = _dec(row.get("quantity")), _dec(row.get("price"))
        summa = _dec(row.get("summa")) if row.get("summa") is not None else qty * price
        lines.append(
            {
                "position": i,
                "product_ref": ref,
                "name": (product or {}).get("name") or mirror.get("name") or ref,
                "thumb_url": mirror.get("thumb_url"),
                "unit": mirror.get("unit"),
                "pack_quantity": num(mirror["pack"]) if mirror.get("pack") else None,
                "blocks": _whole_blocks(qty, mirror.get("pack"), row.get("block")),
                "quantity": num(qty),
                "price": num(price),
                "discount": num(_dec(row.get("discount"))),
                "sum": num(summa),
            }
        )
    total = _dec(extra.get("totalSumma")) or sum((_dec(x["sum"]) for x in lines), Decimal(0))
    discount = _dec(extra.get("discountSumma"))
    due = _dec(extra.get("totalSummaAfterDiscount")) or total - discount
    client = await db.get(SdClient, order.client_id)
    names = await _header_names(
        db,
        _ref(extra.get("agent")) or order.agent_sd_id,
        _ref(extra.get("store")) or order.warehouse_sd_id,
        _ref(extra.get("priceType")) or order.price_type_sd_id,
        _ref(extra.get("trade")) or order.trade_sd_id,
    )
    client_raw = sd_order.client.model_extra if sd_order.client else None
    document = extra.get("dateDocument")
    return {
        "source": "sd",
        "warning": None,
        "number": order_number(
            str(sd_order.SD_id) if sd_order.SD_id else order.sd_id, order.code_1c
        ),
        "sd_id": str(sd_order.SD_id) if sd_order.SD_id else order.sd_id,
        "code": order.code_1c,
        "sd_status": sd_order.status,
        "date": sd_order.dateCreate or _local_time(order.sent_at),
        "shipment_date": str(document)[:10] if document else _iso(order.shipment_date),
        "client": _client_out(client, (client_raw or {}).get("clientName")),
        **names,
        "comment": sd_order.comment if sd_order.comment is not None else order.comment or None,
        "lines": lines,
        "total_qty": num(sum((_dec(x["quantity"]) for x in lines), Decimal(0))),
        "total_sum": num(total),
        "discount_sum": num(discount),
        "total_due": num(due),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def _from_local(db: AsyncSession, order: ShopOrder, warning: str) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(ShopOrderLine, SdProduct.sd_id)
            .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
            .where(ShopOrderLine.order_id == order.id)
            .order_by(ShopOrderLine.position, ShopOrderLine.id)
        )
    ).all()
    products = await _products(db, {ref for _, ref in rows})
    lines = [
        {
            "position": i,
            "product_ref": ref,
            "name": products.get(ref, {}).get("name") or ref,
            "thumb_url": products.get(ref, {}).get("thumb_url"),
            "unit": products.get(ref, {}).get("unit"),
            "pack_quantity": num(products[ref]["pack"])
            if products.get(ref, {}).get("pack")
            else None,
            "blocks": line.blocks,
            "quantity": num(line.quantity),
            "price": num(line.price),
            "discount": 0,
            "sum": num(line.line_sum),
        }
        for i, (line, ref) in enumerate(rows, start=1)
    ]
    client = await db.get(SdClient, order.client_id)
    names = await _header_names(
        db, order.agent_sd_id, order.warehouse_sd_id, order.price_type_sd_id, order.trade_sd_id
    )
    return {
        "source": "local",
        "warning": warning,
        "number": order_number(order.sd_id, order.code_1c),
        "sd_id": order.sd_id,
        "code": order.code_1c,
        "sd_status": order.sd_status,
        "date": _local_time(order.sent_at or order.submitted_at),
        "shipment_date": _iso(order.shipment_date),
        "client": _client_out(client),
        **names,
        "comment": order.comment or None,
        "lines": lines,
        "total_qty": num(order.total_qty),
        "total_sum": num(order.total_sum),
        "discount_sum": 0,
        "total_due": num(order.total_sum),
    }


async def build_invoice(
    db: AsyncSession, sd: SalesDoctorClient, redis: Redis, order: ShopOrder
) -> dict[str, Any]:
    """Waybill data for a sent order (409 ``not_sent`` before Sales Doctor accepted it)."""
    if order.sd_id is None or order.state not in ("sent", "cancelled"):
        raise problem(409, "not_sent", message="The order has not been accepted by Sales Doctor")
    key = _cache_key(order)
    try:
        cached = await redis.get(key)
    except Exception:  # cache is an optimisation only
        cached = None
    if cached:
        data: dict[str, Any] = json.loads(cached)
        return data
    try:
        sd_order = await find_sd_order(sd, order)
    except SDError as e:
        log.warning("invoice_sd_unavailable", order=order.code_1c, error=type(e).__name__)
        return await _from_local(db, order, "sd_unavailable")
    if sd_order is None:
        return await _from_local(db, order, "sd_not_found")
    data = await _from_sd(db, order, sd_order)
    try:
        await redis.set(key, json.dumps(data, default=str), ex=CACHE_TTL)
    except Exception:
        log.warning("invoice_cache_write_failed", order=order.code_1c)
    return data

"""Seed a throw-away database for E2E / load tests (never run against production).

Usage (inside the api container of the e2e stack)::

    python -m e2e.seed --admin-password '...' [--load]

Wipes every application table (refuses unless ENV is local/test and the database name ends
with ``_e2e``), then inserts reference data, products with prices/stock/barcodes, the test
shop with its phone, an access link and a superadmin ``e2e-admin``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import make_url

import app.models  # noqa: F401  (register tables)
from app.core import clock
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import create_engine, create_sessionmaker
from app.domain.phone import evaluate_phone
from app.models import (
    AccessLink,
    AdminUser,
    ClientPhone,
    PhoneFlag,
    ProductBarcode,
    SdAgent,
    SdClient,
    SdPrice,
    SdPriceType,
    SdProduct,
    SdProductCategory,
    SdStock,
    SdTradeDirection,
    SdWarehouse,
)
from e2e.fixtures import (
    AGENT,
    INITIAL_STOCK,
    LINK_SLUG,
    LOAD_CLIENTS,
    LOAD_PRODUCTS,
    PRICE_TYPE,
    PRODUCTS,
    SHOP_PHONE_KEY,
    TRADE,
    WAREHOUSE,
    load_phone,
)

ADMIN = "e2e-admin"
LINK_SETTINGS = {
    "warehouses": {"mode": "fixed", "ids": [WAREHOUSE]},
    "price_types": {"mode": "fixed", "ids": [PRICE_TYPE]},
    "agents": {"mode": "fixed", "ids": [AGENT]},
    "trade_directions": {"mode": "fixed", "ids": [TRADE]},
}


async def seed(admin_password: str, load: bool) -> None:
    settings = get_settings()
    db_name = make_url(settings.DATABASE_URL).database or ""
    if settings.ENV not in ("local", "test") or not db_name.endswith("_e2e"):
        sys.exit("refusing to seed: ENV must be local/test and the database name end in _e2e")
    engine = create_engine(settings.DATABASE_URL)
    now = clock.utcnow()
    common = {"active": True, "raw": {}, "synced_at": now}
    async with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    async with create_sessionmaker(engine)() as db:
        wh = SdWarehouse(sd_id=WAREHOUSE, name="Основной склад", **common)
        pt = SdPriceType(sd_id=PRICE_TYPE, name="НАЛ", kind="sale", **common)
        db.add_all(
            [
                wh,
                pt,
                SdAgent(sd_id=AGENT, name="MANSUR", **common),
                SdTradeDirection(sd_id=TRADE, name="Parfum Brend", **common),
                SdProductCategory(sd_id="c1", name="Гигиена", **common),
                SdProductCategory(sd_id="c2", name="Уход", **common),
            ]
        )
        await db.flush()
        specs = list(PRODUCTS)
        if load:
            specs += [
                (f"load_p{i}", f"Load product {i:03d}", f"47900{i:08d}", 10, 100 + i, 0, "c1")
                for i in range(LOAD_PRODUCTS)
            ]
        for sd_id, name, barcode, pack, price, _stock, cat in specs:
            p = SdProduct(
                sd_id=sd_id,
                name=name,
                pack_quantity=Decimal(pack) if pack else None,
                category_sd_id=cat,
                barcode=barcode,
                **common,
            )
            db.add(p)
            await db.flush()
            if barcode:
                db.add(ProductBarcode(product_id=p.id, barcode=barcode, source="sd"))
            db.add(
                SdPrice(price_type_id=pt.id, product_id=p.id, price=Decimal(price), synced_at=now)
            )
            db.add(
                SdStock(
                    warehouse_id=wh.id,
                    product_id=p.id,
                    quantity=Decimal(str(INITIAL_STOCK[sd_id])),
                    synced_at=now,
                )
            )
        phones = [("w3_7448", "Тест Дилшод", SHOP_PHONE_KEY)]
        if load:
            phones += [
                (f"load_c{i}", f"Load shop {i:02d}", load_phone(i)[3:]) for i in range(LOAD_CLIENTS)
            ]
        for sd_id, name, key in phones:
            c = SdClient(
                sd_id=sd_id, name=name, address="Bukhara", agents=[{"id": AGENT}], **common
            )
            db.add(c)
            await db.flush()
            db.add(ClientPhone(client_id=c.id, phone_key=key, source="sd"))
            status, reason = evaluate_phone(key, 1, settings.PHONE_MAX_CLIENTS)
            db.add(
                PhoneFlag(
                    phone_key=key, status=status, reason=reason, client_count=1, updated_at=now
                )
            )
        admin = AdminUser(
            username=ADMIN,
            full_name="E2E Admin",
            password_hash=hash_password(admin_password),
            role="superadmin",
        )
        db.add(admin)
        await db.flush()
        db.add(
            AccessLink(slug=LINK_SLUG, name="E2E link", settings=LINK_SETTINGS, created_by=admin.id)
        )
        await db.commit()
    await engine.dispose()
    print(f"seeded: {len(specs)} products, {len(phones)} shops, link /s/{LINK_SLUG}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--load", action="store_true", help="add generated load-test data")
    args = ap.parse_args(argv)
    asyncio.run(seed(args.admin_password, args.load))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.salesdoctor import SDMethodError
from app.models import (
    ClientPhone,
    PhoneFlag,
    ProductBarcode,
    SdBrand,
    SdClient,
    SdClientBalance,
    SdPaymentType,
    SdPrice,
    SdPriceType,
    SdProduct,
    SdStock,
    SdWarehouse,
    SyncRun,
)
from app.sync.products import media_url, sd_origin
from app.sync.service import ENTITIES, run_sync
from tests.conftest import ok, sd_error
from tests.sync.conftest import FakeSD, client, ent, product, stock_resp

pytestmark = pytest.mark.usefixtures("fake_sd")


def wh(sd_id: str, name: str, active: str = "Y") -> dict[str, Any]:
    return {"CS_id": sd_id, "SD_id": sd_id, "code_1C": None, "name": name, "active": active}


def price_type(sd_id: str, kind: str, active: str = "Y") -> dict[str, Any]:
    return {**wh(sd_id, f"PT {sd_id}", active), "type": kind, "paymentType": ent("d0_3")}


def set_refs(
    sd: FakeSD,
    warehouses: list[dict[str, Any]] | None = None,
    price_types: list[dict[str, Any]] | None = None,
) -> None:
    sd.set_list(
        "getWarehouse", "warehouse", warehouses if warehouses is not None else [wh("w1", "Main")]
    )
    sd.set_list("getPriceType", "priceType", price_types if price_types is not None else [])
    sd.set_list("getTradeDirection", "tradeDirection", [wh("1", "Trade")])
    sd.set_list("getPaymentType", "currency", [wh("pay1", "Cash")])
    sd.set_list("getAgent", "agent", [{**wh("a1", "Agent"), "user": [{"id": 1}]}])
    sd.set_list("getUnit", "unit", [wh("u1", "pcs")])
    sd.set_list("getProductCategory", "productCategory", [wh("c1", "Cat")])
    sd.set_list("getBrand", "brand", [])


async def count(session: AsyncSession, model: Any, *where: Any) -> int:
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


# ------------------------------------------------------------------ references


async def test_references_insert_update_deactivate(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(
        fake_sd,
        [wh("w1", "Main"), wh("w2", "Second")],
        [price_type("pt1", "sale"), price_type("pt2", "purchase")],
    )
    res = await run_sync(sync_ctx, "references")
    assert res.status == "ok", res.error
    assert await count(session, SdWarehouse) == 2
    pt = (await session.scalars(select(SdPriceType).where(SdPriceType.sd_id == "pt2"))).one()
    assert (
        pt.kind == "purchase" and pt.payment_type_sd_id == "d0_3" and pt.raw["type"] == "purchase"
    )
    assert (await session.scalars(select(SdPaymentType))).one().name == "Cash"  # "currency" key

    set_refs(fake_sd, [wh("w1", "Main renamed")], [price_type("pt1", "sale")])
    assert (await run_sync(sync_ctx, "references")).status == "ok"
    session.expire_all()
    rows = {w.sd_id: w for w in await session.scalars(select(SdWarehouse))}
    assert rows["w1"].name == "Main renamed" and rows["w1"].active is True
    assert rows["w2"].active is False  # not hard-deleted
    assert await count(session, SdBrand) == 0  # empty brands fine, no failure


async def test_references_empty_response_does_not_deactivate(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(fake_sd)
    await run_sync(sync_ctx, "references")
    set_refs(fake_sd, warehouses=[])
    res = await run_sync(sync_ctx, "references")
    assert res.status == "ok" and res.warnings >= 1
    assert (await session.scalars(select(SdWarehouse))).one().active is True


# ------------------------------------------------------------------ products


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/upload/adtProduct/a.jpeg", "https://sd.example/upload/adtProduct/a.jpeg"),
        ("https://cdn.example/a.jpeg", "https://cdn.example/a.jpeg"),
        ("http://i/1.jpg", "http://i/1.jpg"),
        ("  ", None),
        (None, None),
        ("//evil.example/a.jpeg", None),
        ("javascript:alert(1)", None),
        ("a.jpeg", None),
    ],
)
def test_media_url(value: Any, expected: str | None) -> None:
    assert media_url(value, "https://sd.example") == expected


def test_sd_origin() -> None:
    assert sd_origin("https://buxdiler.salesdoc.io/api/v2/") == "https://buxdiler.salesdoc.io"


async def test_products_relative_image_paths_become_absolute(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    raw = product("p1", imageUrl="/upload/adtProduct/p1.jpeg", thumbUrl="/upload/t/p1.jpeg")
    fake_sd.set_list("getProduct", "product", [raw], paged=True)
    assert (await run_sync(sync_ctx, "products")).status == "ok"
    p1 = (await session.scalars(select(SdProduct))).one()
    origin = sd_origin(sync_ctx.settings.SD_BASE_URL)
    assert p1.image_url == f"{origin}/upload/adtProduct/p1.jpeg"
    assert p1.thumb_url == f"{origin}/upload/t/p1.jpeg"


async def test_products_upsert_barcodes_admin_preserved(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.set_list(
        "getProduct",
        "product",
        [product("p1", "Brush", "111"), product("p2", "Paste", "111"), product("p3", "NoCode")],
        paged=True,
    )
    res = await run_sync(sync_ctx, "products")
    assert res.status == "ok" and res.items == 3
    p1 = (await session.scalars(select(SdProduct).where(SdProduct.sd_id == "p1"))).one()
    assert p1.pack_quantity == 12 and p1.category_sd_id == "c1" and p1.brand_sd_id is None
    assert p1.sub_category_sd_id is None and p1.image_url == "http://i/1.jpg" and p1.active
    # duplicate barcode across products is allowed
    assert await count(session, ProductBarcode, ProductBarcode.barcode == "111") == 2

    session.add(ProductBarcode(product_id=p1.id, barcode="ADMIN1", source="admin"))
    await session.commit()

    fake_sd.set_list("getProduct", "product", [product("p1", "Brush", "222")], paged=True)
    assert (await run_sync(sync_ctx, "products")).status == "ok"
    session.expire_all()
    codes = {(b.barcode, b.source) for b in await session.scalars(select(ProductBarcode))}
    assert codes == {("222", "sd"), ("ADMIN1", "admin")}
    inactive = (await session.scalars(select(SdProduct).where(SdProduct.sd_id == "p2"))).one()
    assert inactive.active is False


# ------------------------------------------------------------------ stock


async def seed_catalog(sync_ctx: Any, fake_sd: FakeSD) -> None:
    set_refs(fake_sd)
    await run_sync(sync_ctx, "references")
    fake_sd.set_list("getProduct", "product", [product("p1"), product("p2")], paged=True)
    await run_sync(sync_ctx, "products")


async def test_stock_upsert_and_zeroing(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    await seed_catalog(sync_ctx, fake_sd)
    fake_sd.responses["getStock"] = stock_resp({"w1": [("p1", 5), ("p2", 7), ("ghost", 1)]})
    res = await run_sync(sync_ctx, "stock")
    assert res.status == "ok" and res.items == 2 and res.warnings == 1

    fake_sd.responses["getStock"] = stock_resp({"w1": [("p1", 4)]})  # p2 vanished
    await run_sync(sync_ctx, "stock")
    session.expire_all()
    qty = {r.product_id: r.quantity for r in await session.scalars(select(SdStock))}
    p1 = (await session.scalars(select(SdProduct).where(SdProduct.sd_id == "p1"))).one()
    assert sorted(qty.values()) == [0, 4] and qty[p1.id] == 4


# ------------------------------------------------------------------ prices


async def test_prices_only_active_sale_types_and_cleanup(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(
        fake_sd,
        price_types=[
            price_type("ptA", "sale"),
            price_type("ptB", "sale", active="N"),
            price_type("ptC", "purchase"),
        ],
    )
    await run_sync(sync_ctx, "references")
    fake_sd.set_list("getProduct", "product", [product("p1"), product("p2")], paged=True)
    await run_sync(sync_ctx, "products")

    types = {t.sd_id: t for t in await session.scalars(select(SdPriceType))}
    p = {x.sd_id: x for x in await session.scalars(select(SdProduct))}
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    # legacy rows: purchase + inactive types, and a stale price on the good type
    session.add_all(
        [
            SdPrice(price_type_id=types["ptC"].id, product_id=p["p1"].id, price=1, synced_at=now),
            SdPrice(price_type_id=types["ptB"].id, product_id=p["p1"].id, price=2, synced_at=now),
            SdPrice(price_type_id=types["ptA"].id, product_id=p["p2"].id, price=3, synced_at=now),
        ]
    )
    await session.commit()

    fake_sd.responses["getPrice"] = ok(
        [
            {"product": ent("p1"), "price": 1300.5},
            {"product": ent("nope"), "price": 5},
            {"product": ent("p2"), "price": None},
        ]
    )
    res = await run_sync(sync_ctx, "prices")
    assert res.status == "ok" and res.items == 1 and res.warnings == 1
    assert [c["params"]["priceType"]["SD_id"] for c in fake_sd.calls_of("getPrice")] == ["ptA"]
    pt_a_id = types["ptA"].id
    session.expire_all()
    rows = (await session.scalars(select(SdPrice))).all()
    assert len(rows) == 1  # purchase/inactive removed, stale p2 price removed
    assert rows[0].price_type_id == pt_a_id and float(rows[0].price) == 1300.5


# ------------------------------------------------------------------ clients / phones / flags


async def test_clients_phones_and_flags(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    clients = [
        client("c1", "+998 (91) 921-67-22"),
        client("c2", "+998905146655, 998902996625"),
        client("c3", None),
        client("c4", "123"),
        client("c5", "+998 99 999 99 99"),
        # shared by 4 clients (> PHONE_MAX_CLIENTS=3)
        client("s1", "901111122"),
        client("s2", "901111122"),
        client("s3", "901111122"),
        client("s4", "901111122"),
    ]
    fake_sd.set_list("getClient", "client", clients, paged=True)
    res = await run_sync(sync_ctx, "clients")
    assert res.status == "ok" and res.items == 9, res.error

    c1 = (await session.scalars(select(SdClient).where(SdClient.sd_id == "c1"))).one()
    assert c1.tel_raw == "+998 (91) 921-67-22" and c1.agents[0]["id"] == "d0_11"
    assert c1.sd_updated_at is not None and c1.expeditor_sd_id == "e"
    assert await count(session, ClientPhone) == 1 + 2 + 1 + 4
    flags = {f.phone_key: f for f in await session.scalars(select(PhoneFlag))}
    assert flags["919216722"].status == "ok"
    assert (
        flags["999999999"].status == "suspicious" and flags["999999999"].reason == "repeated_digits"
    )
    assert flags["901111122"].status == "suspicious" and flags["901111122"].client_count == 4
    run = (await session.scalars(select(SyncRun).where(SyncRun.entity == "clients"))).one()
    assert run.detail is not None and run.detail["clients_without_valid_phone"] == 2
    assert run.detail["suspicious"] == 2


async def test_phone_flag_admin_override_and_reset(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.set_list(
        "getClient", "client", [client("c1", "999999999"), client("c2", "901234567")], paged=True
    )
    await run_sync(sync_ctx, "clients")
    from datetime import UTC, datetime

    # admin blocks a normal number and allows a fake one
    for key, status in (("901234567", "blocked"), ("999999999", "allowed")):
        f = await session.get(PhoneFlag, key)
        assert f is not None
        f.status, f.reason, f.updated_at = status, "admin", datetime.now(UTC)
    await session.commit()

    await run_sync(sync_ctx, "clients")  # re-sync must not overwrite admin decisions
    session.expire_all()
    flags = {f.phone_key: f for f in await session.scalars(select(PhoneFlag))}
    assert flags["901234567"].status == "blocked" and flags["901234567"].reason == "admin"
    assert flags["999999999"].status == "allowed" and flags["999999999"].client_count == 1


async def test_sd_phones_replaced_admin_phones_kept(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.set_list("getClient", "client", [client("c1", "901111111")], paged=True)
    await run_sync(sync_ctx, "clients")
    c1 = (await session.scalars(select(SdClient))).one()
    session.add(ClientPhone(client_id=c1.id, phone_key="907777777", source="admin"))
    await session.commit()
    fake_sd.set_list("getClient", "client", [client("c1", "902222222")], paged=True)
    await run_sync(sync_ctx, "clients")
    session.expire_all()
    keys = {(p.phone_key, p.source) for p in await session.scalars(select(ClientPhone))}
    assert keys == {("902222222", "sd"), ("907777777", "admin")}


# ------------------------------------------------------------------ balances


async def test_balances_zero_fill(sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession) -> None:
    fake_sd.set_list(
        "getClient",
        "client",
        [client("c1", None), client("c2", None), client("c3", None)],
        paged=True,
    )
    await run_sync(sync_ctx, "clients")

    def bal(sd_id: str, amount: float) -> dict[str, Any]:
        return {
            "SD_id": sd_id,
            "name": "x",
            "active": True,
            "balance": amount,
            "by-currency": [{"currency_id": "d0_2", "amount": amount}],
        }

    fake_sd.set_list("getBalance", "balance", [bal("c1", -500.5), bal("ghost", 1)], paged=True)
    res = await run_sync(sync_ctx, "balances")
    assert res.status == "ok" and res.warnings == 1
    rows = {r.client_id: r for r in await session.scalars(select(SdClientBalance))}
    assert len(rows) == 3  # absent clients get explicit zero rows
    ids = {c.sd_id: c.id for c in await session.scalars(select(SdClient))}
    assert (
        float(rows[ids["c1"]].balance) == -500.5
        and rows[ids["c1"]].by_currency[0]["amount"] == -500.5
    )
    assert rows[ids["c2"]].balance == 0 and rows[ids["c2"]].by_currency == []

    fake_sd.set_list("getBalance", "balance", [bal("c2", 7)], paged=True)  # c1 dropped out
    await run_sync(sync_ctx, "balances")
    session.expire_all()
    again = {r.client_id: r for r in await session.scalars(select(SdClientBalance))}
    assert again[ids["c1"]].balance == 0 and again[ids["c1"]].by_currency == []
    assert float(again[ids["c2"]].balance) == 7


# ------------------------------------------------------------------ sync_run + locking


async def test_sync_run_records_success(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(fake_sd)
    res = await run_sync(sync_ctx, "references")
    run = (await session.scalars(select(SyncRun))).one()
    assert run.entity == "references" and run.status == "ok" and run.error is None
    assert (
        run.finished_at is not None and run.duration_ms is not None and run.items == res.items > 0
    )
    assert res.run_id == run.id


async def test_sync_run_records_error_without_raising(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.responses["getProduct"] = sd_error(402, "Invalid method")
    res = await run_sync(sync_ctx, "products")
    assert res.status == "error" and "SDMethodError" in (res.error or "")
    run = (await session.scalars(select(SyncRun))).one()
    assert (
        run.status == "error"
        and run.finished_at is not None
        and "Invalid method" in (run.error or "")
    )
    assert issubclass(SDMethodError, Exception)
    # a later good run still works (lock released)
    fake_sd.set_list("getProduct", "product", [product("p1")], paged=True)
    assert (await run_sync(sync_ctx, "products")).status == "ok"


async def test_lock_prevents_overlap(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession, redis: Any
) -> None:
    set_refs(fake_sd)
    await redis.set("sync:lock:references", "other-worker", ex=60)
    res = await run_sync(sync_ctx, "references")
    assert res.status == "skipped"
    assert await count(session, SyncRun) == 0 and fake_sd.calls == []
    await redis.delete("sync:lock:references")
    assert (await run_sync(sync_ctx, "references")).status == "ok"
    assert await redis.exists("sync:lock:references") == 0  # released after run


async def test_entities_registered_and_unknown_rejected(sync_ctx: Any) -> None:
    assert set(ENTITIES) == {"references", "products", "prices", "stock", "clients", "balances"}
    with pytest.raises(KeyError):
        await run_sync(sync_ctx, "nope")

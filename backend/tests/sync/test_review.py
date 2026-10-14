"""db-review hardening: partial fetches, safety caps, per-row validation, retention, ..."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import create_engine
from app.models import (
    ClientPhone,
    SdClient,
    SdClientBalance,
    SdPrice,
    SdProduct,
    SdStock,
    SdWarehouse,
    SyncRun,
)
from app.sync.common import dec
from app.sync.service import latest_runs, mark_stale_runs, purge_sync_runs, run_sync
from tests.conftest import TEST_DATABASE_URL, ok
from tests.sync.conftest import FakeSD, client, ent, product, stock_resp
from tests.sync.test_sync import count, price_type, seed_catalog, set_refs, wh

pytestmark = pytest.mark.usefixtures("fake_sd")


# ---------------------------------------------------------------- 1: partial fetch / cap


async def _products(n: int) -> list[dict[str, Any]]:
    return [product(f"p{i}", f"P{i}") for i in range(n)]


async def test_partial_fetch_fails_and_deactivates_nothing(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.set_list("getProduct", "product", await _products(4), paged=True)
    assert (await run_sync(sync_ctx, "products")).status == "ok"

    def truncated(body: dict[str, Any]) -> dict[str, Any]:
        page = body["params"]["page"]
        rows = [product("p0", "P0"), product("p1", "P1")] if page == 1 else []
        return ok({"product": rows}, pagination={"limit": 2, "total": 4, "page": page})

    fake_sd.responses["getProduct"] = truncated
    res = await run_sync(sync_ctx, "products")
    assert res.status == "error" and "SDIncompleteFetchError" in (res.error or "")
    assert await count(session, SdProduct, SdProduct.active.is_(True)) == 4


async def test_repeated_page_fails_sync(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    same = ok(
        {"client": [client("c1", None), client("c2", None)]},
        pagination={"limit": 2, "total": 10, "page": 1},
    )
    fake_sd.responses["getClient"] = same
    res = await run_sync(sync_ctx, "clients")
    assert res.status == "error" and "ignored" in (res.error or "")
    assert await count(session, SdClient) == 0  # nothing written


async def test_deactivation_cap_skips_and_warns(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.set_list("getProduct", "product", await _products(30), paged=True)
    await run_sync(sync_ctx, "products")
    fake_sd.set_list("getProduct", "product", (await _products(30))[:10], paged=True)  # 67% gone
    res = await run_sync(sync_ctx, "products")
    assert res.status == "ok" and res.warnings >= 1
    assert await count(session, SdProduct, SdProduct.active.is_(True)) == 30
    run = (await session.scalars(select(SyncRun).where(SyncRun.id == res.run_id))).one()
    assert run.detail is not None
    assert run.detail["product_deactivation_skipped_info"]["would_deactivate"] == 20

    # a small, proportionate drop (2 of 30 -> under allowance) is applied
    fake_sd.set_list("getProduct", "product", (await _products(30))[:28], paged=True)
    await run_sync(sync_ctx, "products")
    assert await count(session, SdProduct, SdProduct.active.is_(True)) == 28


# ---------------------------------------------------------------- 2-4: balances/prices/stock


async def test_balances_empty_response_is_noop(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.set_list("getClient", "client", [client("c1", None)], paged=True)
    await run_sync(sync_ctx, "clients")
    fake_sd.set_list(
        "getBalance",
        "balance",
        [{"SD_id": "c1", "balance": 5, "by-currency": [{"currency_id": "x", "amount": 5}]}],
        paged=True,
    )
    await run_sync(sync_ctx, "balances")
    fake_sd.set_list("getBalance", "balance", [], paged=True)
    res = await run_sync(sync_ctx, "balances")
    assert res.status == "ok" and res.warnings == 1 and res.items == 0
    assert float((await session.scalars(select(SdClientBalance.balance))).one()) == 5


async def test_prices_no_sellable_types_deletes_nothing(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(fake_sd, price_types=[price_type("ptA", "sale")])
    await run_sync(sync_ctx, "references")
    fake_sd.set_list("getProduct", "product", [product("p1")], paged=True)
    await run_sync(sync_ctx, "products")
    fake_sd.responses["getPrice"] = ok([{"product": ent("p1"), "price": 10}])
    await run_sync(sync_ctx, "prices")
    assert await count(session, SdPrice) == 1

    set_refs(fake_sd, price_types=[price_type("ptA", "sale", active="N")])
    await run_sync(sync_ctx, "references")  # only type became inactive (1 row, allowance)
    res = await run_sync(sync_ctx, "prices")
    assert res.status == "ok" and res.warnings == 1
    assert await count(session, SdPrice) == 1  # NOT wiped


async def test_prices_cleanup_skipped_for_empty_or_shrunk_list(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(fake_sd, price_types=[price_type("ptA", "sale")])
    await run_sync(sync_ctx, "references")
    fake_sd.set_list("getProduct", "product", await _products(10), paged=True)
    await run_sync(sync_ctx, "products")
    fake_sd.responses["getPrice"] = ok(
        [{"product": ent(f"p{i}"), "price": i + 1} for i in range(10)]
    )
    await run_sync(sync_ctx, "prices")
    assert await count(session, SdPrice) == 10

    fake_sd.responses["getPrice"] = ok([])  # empty -> keep everything
    res = await run_sync(sync_ctx, "prices")
    assert res.warnings == 1 and await count(session, SdPrice) == 10

    fake_sd.responses["getPrice"] = ok([{"product": ent("p0"), "price": 99}])  # 10% of current
    await run_sync(sync_ctx, "prices")
    assert await count(session, SdPrice) == 10

    fake_sd.responses["getPrice"] = ok([{"product": ent(f"p{i}"), "price": 1} for i in range(8)])
    await run_sync(sync_ctx, "prices")  # 80% -> stale ones removed
    assert await count(session, SdPrice) == 8


async def test_price_scale_four_decimals(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(fake_sd, price_types=[price_type("ptA", "sale")])
    await run_sync(sync_ctx, "references")
    fake_sd.set_list("getProduct", "product", [product("p1")], paged=True)
    await run_sync(sync_ctx, "products")
    fake_sd.responses["getPrice"] = ok([{"product": ent("p1"), "price": 12.3456}])
    await run_sync(sync_ctx, "prices")
    assert (await session.scalars(select(SdPrice.price))).one() == Decimal("12.3456")


async def test_stock_empty_payload_never_zeroes(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    await seed_catalog(sync_ctx, fake_sd)
    fake_sd.responses["getStock"] = stock_resp({"w1": [("p1", 5)]})
    await run_sync(sync_ctx, "stock")
    fake_sd.responses["getStock"] = stock_resp({})
    res = await run_sync(sync_ctx, "stock")
    assert res.status == "ok" and res.warnings >= 1
    assert (await session.scalars(select(SdStock.quantity))).one() == 5


async def test_stock_zeroes_only_warehouses_in_payload(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(fake_sd, warehouses=[wh("w1", "A"), wh("w2", "B")])
    await run_sync(sync_ctx, "references")
    fake_sd.set_list("getProduct", "product", [product("p1"), product("p2")], paged=True)
    await run_sync(sync_ctx, "products")
    fake_sd.responses["getStock"] = stock_resp({"w1": [("p1", 5), ("p2", 5)], "w2": [("p1", 9)]})
    await run_sync(sync_ctx, "stock")
    fake_sd.responses["getStock"] = stock_resp({"w1": [("p1", 4)]})  # w2 absent entirely
    await run_sync(sync_ctx, "stock")
    session.expire_all()
    rows = {
        (w, p): q
        for w, p, q in (
            await session.execute(
                select(SdWarehouse.sd_id, SdProduct.sd_id, SdStock.quantity)
                .join(SdStock, SdStock.warehouse_id == SdWarehouse.id)
                .join(SdProduct, SdProduct.id == SdStock.product_id)
            )
        ).all()
    }
    assert rows == {("w1", "p1"): 4, ("w1", "p2"): 0, ("w2", "p1"): 9}


# ---------------------------------------------------------------- 6: per-row validation


def test_dec_rejects_non_finite_and_huge() -> None:
    for bad in (float("nan"), float("inf"), "NaN", "Infinity", "-Infinity", 1e30, "abc", True):
        assert dec(bad) is None
    assert dec("12.5") == Decimal("12.5") and dec(0) == 0


async def test_bad_rows_do_not_abort_clients(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    rows = [
        client("c1", "901111111", lat=41.3, lon=69.2),
        client("c2", "902222222", lat=999, lon=-500),
        client("c3", "903333333", lat="Infinity", lon="NaN"),
        client("c4", "904444444", lat=1e30, lon=0),
        client("c5", "905555555", agents="oops", createdAt="garbage"),
    ]
    fake_sd.set_list("getClient", "client", rows, paged=True)
    res = await run_sync(sync_ctx, "clients")
    assert res.status == "ok" and res.items == 5 and res.warnings >= 3, res.error
    got = {c.sd_id: c for c in await session.scalars(select(SdClient))}
    assert got["c1"].lat == Decimal("41.3") and got["c1"].lon == Decimal("69.2")
    for k in ("c2", "c3", "c4"):
        assert got[k].lat is None or got[k].sd_id == "c1"
    assert got["c2"].lat is None and got["c2"].lon is None
    assert got["c5"].agents == [] and got["c5"].sd_created_at is None


# ---------------------------------------------------------------- 7/8: sync_run upkeep


async def test_mark_stale_runs(sync_ctx: Any, session: AsyncSession) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            SyncRun(entity="stock", started_at=now - timedelta(hours=2), status="running"),
            SyncRun(entity="stock", started_at=now - timedelta(seconds=30), status="running"),
        ]
    )
    await session.commit()
    assert await mark_stale_runs(sync_ctx) == 1
    session.expire_all()
    statuses = sorted(
        (r.status, r.error) for r in await session.scalars(select(SyncRun).order_by(SyncRun.id))
    )
    assert statuses == [("error", "stale: worker restarted"), ("running", None)]


async def test_purge_and_latest_runs(sync_ctx: Any, session: AsyncSession) -> None:
    now = datetime.now(UTC)
    session.add_all(
        [
            SyncRun(entity="stock", started_at=now - timedelta(days=40), status="ok"),
            SyncRun(entity="stock", started_at=now - timedelta(days=1), status="ok", items=1),
            SyncRun(entity="stock", started_at=now - timedelta(hours=1), status="ok", items=2),
            SyncRun(entity="clients", started_at=now - timedelta(days=2), status="error"),
        ]
    )
    await session.commit()
    assert await purge_sync_runs(sync_ctx) == 1
    data = await latest_runs(sync_ctx)
    assert set(data["latest"]) == {"stock", "clients"}
    assert data["latest"]["stock"]["items"] == 2
    assert len(data["recent"]) == 3
    assert await session.scalar(select(func.count()).select_from(SyncRun)) == 3


# ---------------------------------------------------------------- 13: unchanged rows untouched


async def _updated(session: AsyncSession, table: str) -> int:
    return int(
        await session.scalar(text(f"select pg_stat_get_xact_tuples_updated('{table}'::regclass)"))
        or 0
    )


async def test_unchanged_rows_are_not_rewritten(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    await seed_catalog(sync_ctx, fake_sd)
    fake_sd.responses["getStock"] = stock_resp({"w1": [("p1", 5), ("p2", 7)]})
    await run_sync(sync_ctx, "stock")
    base = await _updated(session, "sd_stock")
    await run_sync(sync_ctx, "stock")
    assert await _updated(session, "sd_stock") == base  # identical payload -> zero updates
    fake_sd.responses["getStock"] = stock_resp({"w1": [("p1", 6), ("p2", 7)]})
    await run_sync(sync_ctx, "stock")
    assert await _updated(session, "sd_stock") == base + 1  # only the changed row

    prod_base = await _updated(session, "sd_product")
    fake_sd.set_list("getProduct", "product", [product("p1"), product("p2")], paged=True)
    await run_sync(sync_ctx, "products")
    assert await _updated(session, "sd_product") == prod_base


# ---------------------------------------------------------------- 15: missing active


async def test_missing_active_keeps_existing_value(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    set_refs(fake_sd, [wh("w1", "A", active="N")])
    await run_sync(sync_ctx, "references")
    no_active = {"CS_id": "w1", "SD_id": "w1", "code_1C": None, "name": "A2"}
    fresh = {"CS_id": "w9", "SD_id": "w9", "code_1C": None, "name": "New"}
    set_refs(fake_sd, [no_active, fresh])
    res = await run_sync(sync_ctx, "references")
    assert res.status == "ok" and res.warnings >= 2
    rows = {w.sd_id: w for w in await session.scalars(select(SdWarehouse))}
    session.expire_all()
    rows = {w.sd_id: w for w in await session.scalars(select(SdWarehouse))}
    assert rows["w1"].active is False and rows["w1"].name == "A2"  # kept inactive, still updated
    assert rows["w9"].active is True  # new row gets the default


# ---------------------------------------------------------------- 16: phone_key check


async def test_phone_key_check_constraint(
    sync_ctx: Any, fake_sd: FakeSD, session: AsyncSession
) -> None:
    fake_sd.set_list("getClient", "client", [client("c1", "901111111")], paged=True)
    await run_sync(sync_ctx, "clients")
    cid = (await session.scalars(select(SdClient.id))).one()
    for bad in ("12345678", "12345678a", "1234567890", " 12345678"):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(ClientPhone(client_id=cid, phone_key=bad, source="admin"))
                await session.flush()
    assert await count(session, ClientPhone) == 1


# ---------------------------------------------------------------- 5: worker engine settings


async def test_engine_server_settings_applied() -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("no test database")
    engine = create_engine(
        TEST_DATABASE_URL,
        server_settings={"lock_timeout": "5000", "idle_in_transaction_session_timeout": "60000"},
    )
    async with engine.connect() as conn:
        assert await conn.scalar(text("show lock_timeout")) == "5s"
        assert await conn.scalar(text("show idle_in_transaction_session_timeout")) == "1min"
    await engine.dispose()

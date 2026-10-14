"""Order test harness: a stateful fake Sales Doctor (respx), recording job queue, seeded catalog."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import fakeredis
import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import clock
from app.integrations.salesdoctor import SalesDoctorClient, SDCredentials, TokenStore
from app.models import (
    AccessLink,
    AdminUser,
    ProductBarcode,
    ProductSetting,
    SdClient,
    SdPrice,
    SdPriceType,
    SdProduct,
    SdProductCategory,
    SdStock,
    SdWarehouse,
)
from app.sync.context import SyncContext
from tests.api.conftest import (  # noqa: F401  (re-exported fixtures)
    Api,
    ApiFactory,
    _fast_argon2,
    app_factory,
    make_settings,
    seed,
)
from tests.api.helpers import Seed
from tests.conftest import BASE_URL, ok, sd_error

PHONE = "998902996625"
KEY = "902996625"
SLUG = "shop-link"
WH = "d0_1"


class FakeOrderSD:
    """Canned SD for orders: stateful stock + orders; ``script`` queues write outcomes."""

    def __init__(self) -> None:
        self.stock: dict[str, dict[str, float]] = {WH: {}}
        self.orders: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.script: list[str | dict[str, Any]] = []  # consumed by setOrder, one per call
        self.deleted: list[str] = []
        self.stock_down = False
        self.orders_down = False
        self.before_set_order: Callable[[], Awaitable[None]] | None = None
        # one-shot async hooks run at the start of the next call of a method (e.g. "getOrder")
        self.hooks: dict[str, Callable[[], Awaitable[None]]] = {}
        self.delete_script: list[str] = []  # "timeout" | "error", one per setDeletedOrder
        self.status_script: list[str] = []  # "timeout", one per setStatus
        self._seq = 53000

    def calls_of(self, method: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method]

    def set_orders_sent(self) -> list[dict[str, Any]]:
        return [o for c in self.calls_of("setOrder") for o in c["data"]["order"]]

    # -- behaviour -----------------------------------------------------------------------

    def _reserve(self, order: dict[str, Any], sign: int) -> None:
        wh = order["warehouse"]["SD_id"]
        for line in order["orderProducts"]:
            ref = line["product"]["SD_id"]
            self.stock.setdefault(wh, {})
            self.stock[wh][ref] = self.stock[wh].get(ref, 0) + sign * line["quantity"]

    def apply_order(self, body: dict[str, Any]) -> dict[str, Any]:
        code = body["code_1C"]
        old = self.orders.get(code)
        if old is not None and old["status"] != 5:
            self._reserve(old, +1)
        sd_id = old["SD_id"] if old else f"d0_{self._seq}"
        if old is None:
            self._seq += 1
        stored = {**body, "SD_id": sd_id}
        self.orders[code] = stored
        if stored["status"] != 5:
            self._reserve(stored, -1)
        return stored

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        method = body["method"]
        hook = self.hooks.pop(method, None)
        if hook is not None:
            await hook()
        if method == "getStock":
            if self.stock_down:
                return httpx.Response(200, json=sd_error(500, "stock down"))
            rows = [
                {
                    "SD_id": wh,
                    "name": wh,
                    "active": True,
                    "products": [{"SD_id": ref, "quantity": qty} for ref, qty in products.items()],
                }
                for wh, products in self.stock.items()
            ]
            return httpx.Response(200, json=ok({"warehouse": rows}))
        if method == "getOrder":
            if self.orders_down:
                return httpx.Response(200, json=sd_error(500, "orders down"))
            rows = [
                {
                    "SD_id": o["SD_id"],
                    "code_1C": code,
                    "status": o["status"],
                    "orderProducts": o["orderProducts"],
                }
                for code, o in self.orders.items()
            ]
            return httpx.Response(
                200,
                json=ok({"order": rows}, pagination={"limit": 200, "total": len(rows), "page": 1}),
            )
        if method == "setOrder":
            return await self._set_order(body)
        if method == "setStatus":
            if (self.status_script.pop(0) if self.status_script else "ok") == "timeout":
                raise httpx.ReadTimeout("slow")
            for change in body["data"]["order"]:
                order = self.orders.get(change["code_1C"])
                if order is None:
                    return httpx.Response(200, json=sd_error(400, "not found"))
                if order["status"] != 5 and change["status"] == 5:
                    self._reserve(order, +1)
                order["status"] = change["status"]
            return httpx.Response(200, json=ok({"completed": 1, "error": 0, "data": {}}))
        if method == "setDeletedOrder":
            action = self.delete_script.pop(0) if self.delete_script else "ok"
            if action == "timeout":
                raise httpx.ReadTimeout("slow")
            if action == "error":
                return httpx.Response(200, json=sd_error(400, "cannot delete"))
            for ref in body["data"]["deletedOrder"]:
                self.deleted.append(ref["code_1C"])
            return httpx.Response(200, json=ok({"completed": 1, "error": 0, "data": {}}))
        raise AssertionError(f"unexpected SD method {method}")

    async def _set_order(self, body: dict[str, Any]) -> httpx.Response:
        if self.before_set_order is not None:
            hook, self.before_set_order = self.before_set_order, None
            await hook()
        action = self.script.pop(0) if self.script else "ok"
        (order,) = body["data"]["order"]
        if action == "timeout":
            raise httpx.ReadTimeout("slow")
        if action == "timeout_applied":
            self.apply_order(order)
            raise httpx.ReadTimeout("slow")
        if action == "connect_error":
            raise httpx.ConnectError("down")
        if action == "validation":
            return httpx.Response(
                200,
                json=sd_error(
                    400,
                    "Validation error",
                    {"orderProduct": [{"index": 0, "errors": ["Product is blocked"]}]},
                ),
            )
        if isinstance(action, dict):  # raw payload
            return httpx.Response(200, json=action)
        stored = self.apply_order(order)
        data = {"order": [{"CS_id": "", "SD_id": stored["SD_id"], "code_1C": order["code_1C"]}]}
        return httpx.Response(200, json=ok({"completed": 1, "error": 0, "data": data}))


class RecordingJobs:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...], float | None]] = []

    async def enqueue(
        self, name: str, *args: Any, defer_by: float | None = None, job_id: str | None = None
    ) -> bool:
        self.enqueued.append((name, args, defer_by))
        return True

    def names(self) -> list[str]:
        return [n for n, _, _ in self.enqueued]


@pytest.fixture
def fake_sd(mock_sd: respx.MockRouter) -> FakeOrderSD:
    server = FakeOrderSD()
    mock_sd.post(BASE_URL).mock(side_effect=server.handler)
    return server


@pytest.fixture
async def osd(fake_sd: FakeOrderSD, store: TokenStore) -> AsyncIterator[SalesDoctorClient]:
    await store.set(SDCredentials("d0_1", "tok"))

    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient() as http:
        yield SalesDoctorClient(
            http,
            store,
            base_url=BASE_URL,
            login="u",
            password=SecretStr("p"),
            max_retries=1,
            sleep=no_sleep,
        )


@pytest.fixture
def jobs() -> RecordingJobs:
    return RecordingJobs()


# ------------------------------------------------------------------------------------ catalog


@dataclass
class Catalog:
    products: dict[str, SdProduct] = field(default_factory=dict)

    def __getitem__(self, ref: str) -> SdProduct:
        return self.products[ref]


async def _product(
    s: AsyncSession,
    ref: str,
    name: str,
    *,
    pack: int | None = None,
    barcodes: tuple[str, ...] = (),
    category: str | None = "c1",
    brand: str | None = None,
    active: bool = True,
    hidden: bool = False,
    volume: float | None = None,
    code: str | None = None,
) -> SdProduct:
    now = clock.utcnow()
    p = SdProduct(
        sd_id=ref,
        name=name,
        code_1c=code,
        active=active,
        raw={},
        synced_at=now,
        pack_quantity=Decimal(pack) if pack else None,
        category_sd_id=category,
        brand_sd_id=brand,
        volume=Decimal(str(volume)) if volume is not None else None,
    )
    s.add(p)
    await s.flush()
    for b in barcodes:
        s.add(ProductBarcode(product_id=p.id, barcode=b, source="sd"))
    if hidden:
        s.add(ProductSetting(product_id=p.id, hidden=True))
    return p


async def seed_catalog(s: AsyncSession) -> Catalog:
    """Products used across the order tests (prices: d0_2 = `price`, d0_4 = `price2`)."""
    from sqlalchemy import select

    now = clock.utcnow()
    s.add(SdProductCategory(sd_id="c2", name="Cat 2", active=True, raw={}, synced_at=now))
    await s.flush()
    specs: dict[str, dict[str, Any]] = {
        # pack of 12, two barcodes, volume 0.5
        "pa": {
            "name": "Cream Alpha",
            "pack": 12,
            "price": 1000,
            "price2": 900,
            "stock": 100,
            "barcodes": ("4780000000011", "4780000000100"),
            "volume": 0.5,
            "code": "CA",
        },
        "pb": {
            "name": "Soap Beta",
            "pack": None,
            "price": 500,
            "price2": 450,
            "stock": 5,
            "barcodes": ("4780000000028",),
            "category": "c2",
            "brand": "b1",
            "volume": 0.25,
        },
        # two products share one barcode (SD has duplicates)
        "pc1": {"name": "Twin One", "price": 300, "stock": 10, "barcodes": ("4780000000035",)},
        "pc2": {"name": "Twin Two", "price": 310, "stock": 10, "barcodes": ("4780000000035",)},
        "pnoprice": {
            "name": "Free Gift",
            "price": None,
            "stock": 10,
            "barcodes": ("4780000000042",),
        },
        "pzero": {"name": "Zero Price", "price": 0, "stock": 10, "barcodes": ("4780000000059",)},
        "pinactive": {
            "name": "Old Item",
            "price": 100,
            "stock": 10,
            "active": False,
            "barcodes": ("4780000000066",),
        },
        "phidden": {
            "name": "Secret Item",
            "price": 100,
            "stock": 10,
            "hidden": True,
            "barcodes": ("4780000000073",),
        },
        "pnostock": {
            "name": "Sold Out",
            "price": 200,
            "stock": None,
            "barcodes": ("4780000000080",),
        },
    }
    out = Catalog()
    ids = {x.sd_id: x.id for x in (await s.scalars(select(SdPriceType))).all()}
    wh_id = await s.scalar(select(SdWarehouse.id).where(SdWarehouse.sd_id == WH))
    for ref, spec in specs.items():
        spec = dict(spec)
        price, price2, stock = spec.pop("price"), spec.pop("price2", None), spec.pop("stock")
        p = await _product(s, ref, spec.pop("name"), **spec)
        out.products[ref] = p
        if price is not None:
            s.add(
                SdPrice(
                    price_type_id=ids["d0_2"],
                    product_id=p.id,
                    price=Decimal(str(price)),
                    synced_at=now,
                )
            )
        if price2 is not None:
            s.add(
                SdPrice(
                    price_type_id=ids["d0_4"],
                    product_id=p.id,
                    price=Decimal(str(price2)),
                    synced_at=now,
                )
            )
        if stock is not None:
            s.add(
                SdStock(
                    warehouse_id=wh_id, product_id=p.id, quantity=Decimal(str(stock)), synced_at=now
                )
            )
    await s.commit()
    return out


# ------------------------------------------------------------------------------------- world


@dataclass
class World:
    shop: Api
    admin: Api
    sd_server: FakeOrderSD
    sd: SalesDoctorClient
    jobs: RecordingJobs
    redis: fakeredis.FakeAsyncRedis
    session: AsyncSession
    sessionmaker: async_sessionmaker[AsyncSession]
    seed: Seed
    catalog: Catalog
    client: SdClient
    link: AccessLink
    root: AdminUser
    sync: SyncContext

    # -- shop helpers --
    async def new_order(self, price_type: str | None = "d0_2", **header: Any) -> dict[str, Any]:
        r = await self.shop.post("/api/shop/orders", {})
        assert r.status_code == 201, r.text
        order: dict[str, Any] = r.json()
        if price_type is not None or header:
            patch = {"version": order["version"], **header}
            if price_type is not None:
                patch["price_type"] = price_type
            r = await self.shop.patch(f"/api/shop/orders/{order['public_id']}", patch)
            assert r.status_code == 200, r.text
            order = r.json()
        return order

    async def set_lines(
        self, order: dict[str, Any], lines: Sequence[tuple[str, int, int | str]]
    ) -> dict[str, Any]:
        body = {
            "version": order["version"],
            "lines": [
                {"product_ref": ref, "blocks": blocks, "pieces": str(pieces)}
                for ref, blocks, pieces in lines
            ],
        }
        r = await self.shop.put(f"/api/shop/orders/{order['public_id']}/lines", body)
        assert r.status_code == 200, r.text
        result: dict[str, Any] = r.json()
        return result

    async def draft(
        self, lines: Sequence[tuple[str, int, int | str]] | None = None
    ) -> dict[str, Any]:
        order = await self.new_order()
        return await self.set_lines(order, lines or [("pb", 0, 2)])

    async def submit(self, order: dict[str, Any], **kw: Any) -> httpx.Response:
        return await self.shop.post(f"/api/shop/orders/{order['public_id']}/submit", **kw)


@pytest.fixture
async def world(
    app_factory: ApiFactory,  # noqa: F811
    seed: Seed,  # noqa: F811
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    osd: SalesDoctorClient,
    fake_sd: FakeOrderSD,
    jobs: RecordingJobs,
    redis: fakeredis.FakeAsyncRedis,
) -> World:
    root = await seed.admin("root")
    client = await seed.client("Тест Дилшод", [KEY], sd_id="w3_7448")
    link = await seed.link(root, SLUG)
    catalog = await seed_catalog(session)
    for ref, qty in (("pa", 100), ("pb", 5), ("pc1", 10), ("pc2", 10)):
        fake_sd.stock[WH][ref] = qty
    settings = make_settings()
    shop = await app_factory(settings, sd=osd, jobs=jobs)
    admin = await app_factory(settings, sd=osd, jobs=jobs)
    assert (await shop.shop_login(SLUG, PHONE)).status_code == 200
    assert (await admin.admin_login("root")).status_code == 200
    sync = SyncContext(sessionmaker, osd, redis, settings)
    return World(
        shop, admin, fake_sd, osd, jobs, redis, session, sessionmaker, seed, catalog,
        client, link, root, sync,
    )  # fmt: skip

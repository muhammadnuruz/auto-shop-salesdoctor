"""Order domain service: header options, lines, repricing, views, submit-time validation.

Prices always come from the SD mirror (``sd_price``) for the order's price type; any price sent by
a client is ignored. All helpers work inside the caller's transaction and never commit.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core import clock
from app.domain.link_settings import LinkSettings, ProductFilter
from app.domain.order_rules import ISSUE_NOT_ALLOWED, product_issues
from app.domain.quantity import QuantityError, compute_quantity, line_sum
from app.models import (
    AccessLink,
    ProductBarcode,
    ProductSetting,
    SdAgent,
    SdClient,
    SdPrice,
    SdPriceType,
    SdProduct,
    SdProductCategory,
    SdStock,
    SdTradeDirection,
    SdUnit,
    SdWarehouse,
    ShopOrder,
    ShopOrderEvent,
    ShopOrderLine,
)
from app.services.eligibility import parse_settings
from app.sync.common import SD_TZ

MAX_LINES = 500
MAX_COMMENT = 500
EDITABLE_STATES = frozenset({"draft", "failed"})
HEADER_FIELDS: dict[str, str] = {
    "agent": "agent_sd_id",
    "warehouse": "warehouse_sd_id",
    "price_type": "price_type_sd_id",
    "trade": "trade_sd_id",
}
UNCATEGORIZED = "—"


def problem(status: int, code: str, **extra: Any) -> HTTPException:
    """HTTPException whose ``detail`` is a machine-readable object."""
    return HTTPException(status, detail={"code": code, **extra})


@dataclass(frozen=True)
class Actor:
    type: str  # shop | admin | system
    id: int | None = None


def num(value: Decimal | int | float | None) -> int | float | None:
    """JSON number for a Decimal (integral values stay ints)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def today_local() -> date:
    return clock.utcnow().astimezone(SD_TZ).date()


# ------------------------------------------------------------------------------ header options


@dataclass
class HeaderOptions:
    warehouses: list[str] = field(default_factory=list)
    price_types: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    trades: list[str] = field(default_factory=list)

    def for_field(self, name: str) -> list[str]:
        return {
            "agent": self.agents,
            "warehouse": self.warehouses,
            "price_type": self.price_types,
            "trade": self.trades,
        }[name]


async def _active_ids(
    db: AsyncSession, model: Any, ids: list[str] | None, *extra: Any
) -> list[str]:
    stmt = select(model.sd_id).where(model.active.is_(True), *extra)
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(model.sd_id.in_(ids))
    found = list((await db.scalars(stmt.order_by(model.name, model.sd_id))).all())
    if ids is None:
        return found
    present = set(found)
    return [i for i in ids if i in present]  # keep the link's configured order


def assigned_agent_ids(client: SdClient, restrict: list[str]) -> list[str]:
    assigned = [
        str(a["id"]) for a in client.agents if isinstance(a, dict) and a.get("id") is not None
    ]
    assigned = list(dict.fromkeys(assigned))
    return [a for a in assigned if a in restrict] if restrict else assigned


async def link_options(db: AsyncSession, client: SdClient, st: LinkSettings) -> HeaderOptions:
    """What the shop may pick under this link (active references only)."""
    agent_ids = (
        assigned_agent_ids(client, st.agents.ids)
        if st.agents.mode == "client_assigned"
        else st.agents.ids
    )
    return HeaderOptions(
        warehouses=await _active_ids(db, SdWarehouse, st.warehouses.ids),
        price_types=await _active_ids(
            db, SdPriceType, st.price_types.ids, SdPriceType.kind == "sale"
        ),
        agents=await _active_ids(db, SdAgent, agent_ids),
        trades=await _active_ids(db, SdTradeDirection, st.trade_directions.ids),
    )


async def admin_options(db: AsyncSession) -> HeaderOptions:
    """Admins may use any active SD reference (never purchase price types)."""
    return HeaderOptions(
        warehouses=await _active_ids(db, SdWarehouse, None),
        price_types=await _active_ids(db, SdPriceType, None, SdPriceType.kind == "sale"),
        agents=await _active_ids(db, SdAgent, None),
        trades=await _active_ids(db, SdTradeDirection, None),
    )


def apply_header(
    order: ShopOrder, patch: dict[str, str | None], options: HeaderOptions
) -> list[dict[str, Any]]:
    """Apply selected references; returns per-field problems (nothing is applied on error)."""
    problems: list[dict[str, Any]] = []
    for name, value in patch.items():
        if value is not None and value not in options.for_field(name):
            problems.append(
                {"field": name, "code": ISSUE_NOT_ALLOWED, "message": f"{name} is not allowed"}
            )
    if problems:
        return problems
    for name, value in patch.items():
        setattr(order, HEADER_FIELDS[name], value)
    return []


def autofill_header(order: ShopOrder, options: HeaderOptions) -> None:
    """Fixed / single-option selections are filled in automatically."""
    for name, column in HEADER_FIELDS.items():
        opts = options.for_field(name)
        if getattr(order, column) is None and len(opts) == 1:
            setattr(order, column, opts[0])


def validate_shipment_date(value: date | None) -> None:
    if value is None:
        return
    today = today_local()
    if value < today or value > today + timedelta(days=180):
        raise problem(422, "bad_shipment_date", message="Shipment date is out of range")


def link_settings_or_409(link: AccessLink) -> LinkSettings:
    st = parse_settings(link)
    if st is None:
        raise problem(409, "link_misconfigured", message="Link settings are invalid")
    return st


# --------------------------------------------------------------------------------- loading


async def get_order(
    db: AsyncSession,
    public_id: uuid.UUID,
    *,
    client_id: int | None = None,
    for_update: bool = False,
) -> ShopOrder:
    """Load by public id; 404 when missing or (if ``client_id``) owned by another client."""
    stmt = select(ShopOrder).where(ShopOrder.public_id == public_id)
    if client_id is not None:
        stmt = stmt.where(ShopOrder.client_id == client_id)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    order = await db.scalar(stmt)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


def check_version(order: ShopOrder, expected: int | None) -> None:
    if expected is not None and expected != order.version:
        raise problem(409, "version_conflict", current_version=order.version)


async def bump_version(db: AsyncSession, order: ShopOrder) -> None:
    """Atomically ``version = version + 1`` where ``version`` still equals the loaded value.

    Optimistic-lock guard at write time: a concurrent writer that got in between makes this a
    409 instead of a silent lost update (the in-memory copy is never trusted on its own).
    """
    expected = order.version
    res = await db.execute(
        update(ShopOrder)
        .where(ShopOrder.id == order.id, ShopOrder.version == expected)
        .values(version=expected + 1, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    if cast_rowcount(res) != 1:
        current = await db.scalar(select(ShopOrder.version).where(ShopOrder.id == order.id))
        raise problem(409, "version_conflict", current_version=current)
    set_committed_value(order, "version", expected + 1)


def cast_rowcount(res: Any) -> int:
    result: CursorResult[Any] = res
    return int(result.rowcount)


def ensure_editable(order: ShopOrder) -> None:
    if order.state not in EDITABLE_STATES:
        raise problem(409, "order_not_editable", state=order.state)


def add_event(
    db: AsyncSession,
    order: ShopOrder,
    event: str,
    actor: Actor,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        ShopOrderEvent(
            order_id=order.id,
            actor_type=actor.type,
            actor_id=actor.id,
            event=event,
            payload=payload,
        )
    )


async def create_order(
    db: AsyncSession,
    *,
    client: SdClient,
    link: AccessLink,
    actor: Actor,
    options: HeaderOptions,
    header: dict[str, str | None],
    shipment_date: date | None = None,
    comment: str = "",
) -> ShopOrder:
    order = ShopOrder(
        public_id=uuid.uuid4(),
        code_1c=f"TMP-{uuid.uuid4().hex}",
        client_id=client.id,
        link_id=link.id,
        created_by_type="admin" if actor.type == "admin" else "shop",
        created_by_admin_id=actor.id if actor.type == "admin" else None,
        shipment_date=shipment_date,
        comment=comment,
        state="draft",
        version=1,
    )
    problems = apply_header(order, header, options)
    if problems:
        raise problem(422, "invalid_header", errors=problems)
    autofill_header(order, options)
    db.add(order)
    await db.flush()
    order.code_1c = f"WEB-{order.id:06d}"
    add_event(db, order, "created", actor, {"link": link.slug})
    await db.flush()
    return order


# ------------------------------------------------------------------------------------ views


@dataclass
class LineView:
    line: ShopOrderLine
    product: SdProduct
    category_name: str | None = None
    unit_name: str | None = None
    price: Decimal | None = None  # price applied to the line (None = no usable price)
    mirror_price: Decimal | None = None  # current sd_price of the order's price type
    stock: Decimal | None = None
    issues: list[str] = field(default_factory=list)
    over_stock: bool = False

    @property
    def warnings(self) -> list[str]:
        out = list(self.issues)
        if self.over_stock:
            out.append("over_stock")
        return out


@dataclass
class OrderView:
    order: ShopOrder
    lines: list[LineView]
    st: LinkSettings | None
    live: bool  # prices/stock/warnings are recomputed (editable states only)

    def totals(self) -> dict[str, Any]:
        qty = sum((v.line.quantity for v in self.lines), Decimal(0))
        total = sum((v.line.line_sum for v in self.lines), Decimal(0))
        volume = sum(
            (v.line.quantity * (v.product.volume or Decimal(0)) for v in self.lines), Decimal(0)
        )
        return {
            "qty": num(qty),
            "sum": num(total),
            "volume": num(volume.quantize(Decimal("0.001"))),
            "lines_count": len(self.lines),
        }

    def categories(self) -> list[dict[str, Any]]:
        groups: dict[str | None, dict[str, Any]] = {}
        for v in self.lines:
            g = groups.setdefault(
                v.product.category_sd_id,
                {
                    "category": v.product.category_sd_id,
                    "name": v.category_name or UNCATEGORIZED,
                    "qty": Decimal(0),
                    "volume": Decimal(0),
                    "sum": Decimal(0),
                },
            )
            g["qty"] += v.line.quantity
            g["volume"] += v.line.quantity * (v.product.volume or Decimal(0))
            g["sum"] += v.line.line_sum
        return [
            {
                **g,
                "qty": num(g["qty"]),
                "volume": num(g["volume"].quantize(Decimal("0.001"))),
                "sum": num(g["sum"]),
            }
            for g in sorted(groups.values(), key=lambda x: str(x["name"]))
        ]


async def _one_id(db: AsyncSession, model: Any, sd_id: str | None) -> int | None:
    if sd_id is None:
        return None
    return await db.scalar(select(model.id).where(model.sd_id == sd_id))


async def price_map(
    db: AsyncSession, price_type_sd_id: str | None, pids: list[int]
) -> dict[int, Decimal]:
    pt = await _one_id(db, SdPriceType, price_type_sd_id)
    if pt is None or not pids:
        return {}
    rows = await db.execute(
        select(SdPrice.product_id, SdPrice.price).where(
            SdPrice.price_type_id == pt, SdPrice.product_id.in_(pids)
        )
    )
    return dict(rows.all())


async def stock_map(
    db: AsyncSession, warehouse_sd_id: str | None, pids: list[int]
) -> dict[int, Decimal]:
    wh = await _one_id(db, SdWarehouse, warehouse_sd_id)
    if wh is None or not pids:
        return {}
    rows = await db.execute(
        select(SdStock.product_id, SdStock.quantity).where(
            SdStock.warehouse_id == wh, SdStock.product_id.in_(pids)
        )
    )
    return dict(rows.all())


async def hidden_ids(db: AsyncSession, pids: list[int]) -> set[int]:
    if not pids:
        return set()
    return set(
        (
            await db.scalars(
                select(ProductSetting.product_id).where(
                    ProductSetting.product_id.in_(pids), ProductSetting.hidden.is_(True)
                )
            )
        ).all()
    )


async def _names(db: AsyncSession, model: Any, ids: set[str | None]) -> dict[str, str | None]:
    clean = [i for i in ids if i]
    if not clean:
        return {}
    rows = await db.execute(select(model.sd_id, model.name).where(model.sd_id.in_(clean)))
    return {str(a): b for a, b in rows.all()}


async def build_view(
    db: AsyncSession,
    order: ShopOrder,
    *,
    st: LinkSettings | None,
    admin: bool = False,
    stock_override: dict[str, Decimal] | None = None,
    credit: dict[int, Decimal] | None = None,
    live: bool | None = None,
    fixed_prices: dict[int, Decimal] | None = None,
    lines: list[tuple[ShopOrderLine, SdProduct]] | None = None,
) -> OrderView:
    """Load lines and compute current price/stock/warnings.

    ``lines`` replaces the DB read with in-memory (line, product) pairs (admin preview: nothing
    is persisted, so the caller must keep those lines and the order out of the session).

    For editable orders (or ``live=True``) the lines' stored price/sum and the order totals are
    refreshed in memory from the mirror; admins are not limited by the link's product filter.
    ``fixed_prices`` (product id -> price) keeps those lines at the given price instead of the
    mirror price (admin edits of existing lines).
    """
    if lines is not None:
        rows: Sequence[Any] = lines
    else:
        rows = (
            await db.execute(
                select(ShopOrderLine, SdProduct)
                .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
                .where(ShopOrderLine.order_id == order.id)
                .order_by(ShopOrderLine.position, ShopOrderLine.id)
            )
        ).all()
    is_live = order.state in EDITABLE_STATES if live is None else live
    views = [LineView(line, product) for line, product in rows]
    pids = [v.product.id for v in views]
    cats = await _names(db, SdProductCategory, {v.product.category_sd_id for v in views})
    units = await _names(db, SdUnit, {v.product.unit_sd_id for v in views})
    for v in views:
        v.category_name = cats.get(v.product.category_sd_id or "")
        v.unit_name = units.get(v.product.unit_sd_id or "")
    if is_live:
        prices = await price_map(db, order.price_type_sd_id, pids)
        stocks = await stock_map(db, order.warehouse_sd_id, pids)
        hidden = await hidden_ids(db, pids)
        flt: ProductFilter | None = None if admin or st is None else st.product_filter
        for v in views:
            price = prices.get(v.product.id)
            v.mirror_price = price if price is not None and price > 0 else None
            if fixed_prices is not None and v.product.id in fixed_prices:
                price = fixed_prices[v.product.id]
            v.price = price if price is not None and price > 0 else None
            if order.warehouse_sd_id is not None:
                if stock_override is not None:
                    v.stock = stock_override.get(v.product.sd_id, Decimal(0))
                else:
                    v.stock = stocks.get(v.product.id, Decimal(0))
            v.issues = product_issues(
                v.product, hidden=v.product.id in hidden, price=price, flt=flt
            )
            allow_over = st.allow_over_stock if st is not None else False
            if v.stock is not None and not allow_over:
                available = v.stock + (credit or {}).get(v.product.id, Decimal(0))
                v.over_stock = v.line.quantity > available
            v.line.price = v.price if v.price is not None else Decimal(0)
            v.line.line_sum = line_sum(v.line.quantity, v.line.price)
        _refresh_totals(order, views)
    else:
        for v in views:
            v.price = v.line.price
    return OrderView(order, views, st, is_live)


def _refresh_totals(order: ShopOrder, views: list[LineView]) -> None:
    order.total_sum = sum((v.line.line_sum for v in views), Decimal(0))
    order.total_qty = sum((v.line.quantity for v in views), Decimal(0))
    order.lines_count = len(views)


# --------------------------------------------------------------------------------- line edits


@dataclass
class LineInput:
    product_ref: str
    blocks: int = 0
    pieces: Decimal = Decimal(0)
    comment: str | None = None


async def resolve_products(db: AsyncSession, refs: list[str]) -> dict[str, SdProduct]:
    if not refs:
        return {}
    rows = (await db.scalars(select(SdProduct).where(SdProduct.sd_id.in_(refs)))).all()
    return {p.sd_id: p for p in rows}


async def line_problems(
    db: AsyncSession,
    order: ShopOrder,
    products: list[SdProduct],
    *,
    st: LinkSettings | None,
    admin: bool,
) -> dict[int, list[str]]:
    """Orderability issues per product id (active, not hidden, in the link filter, priced)."""
    pids = [p.id for p in products]
    prices = await price_map(db, order.price_type_sd_id, pids)
    hidden = await hidden_ids(db, pids)
    flt = None if admin or st is None else st.product_filter
    return {
        p.id: product_issues(p, hidden=p.id in hidden, price=prices.get(p.id), flt=flt)
        for p in products
    }


def _line_error(ref: str, code: str, message: str) -> dict[str, Any]:
    return {"product_ref": ref, "code": code, "message": message}


async def prepare_lines(
    db: AsyncSession,
    order: ShopOrder,
    inputs: list[LineInput],
    *,
    st: LinkSettings | None,
    admin: bool,
) -> list[tuple[SdProduct, LineInput, Decimal]]:
    """Validate line inputs; raises 422 with per-line errors. Returns (product, input, qty)."""
    if order.price_type_sd_id is None:
        raise problem(422, "price_type_required", message="Select a price type first")
    if len(inputs) > MAX_LINES:
        raise problem(422, "too_many_lines", message=f"At most {MAX_LINES} lines")
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in inputs:
        if item.product_ref in seen:
            errors.append(_line_error(item.product_ref, "duplicate_product", "Duplicate line"))
        seen.add(item.product_ref)
    products = await resolve_products(db, [i.product_ref for i in inputs])
    issues = await line_problems(db, order, list(products.values()), st=st, admin=admin)
    out: list[tuple[SdProduct, LineInput, Decimal]] = []
    for item in inputs:
        product = products.get(item.product_ref)
        if product is None:
            errors.append(_line_error(item.product_ref, "unknown_product", "Unknown product"))
            continue
        for issue in issues[product.id]:
            errors.append(_line_error(item.product_ref, issue, issue.replace("_", " ")))
        try:
            qty = compute_quantity(item.blocks, item.pieces, product.pack_quantity)
        except QuantityError as exc:
            errors.append(_line_error(item.product_ref, exc.code, exc.message))
            continue
        if qty <= 0:
            errors.append(_line_error(item.product_ref, "zero_quantity", "Quantity must be > 0"))
            continue
        out.append((product, item, qty))
    if errors:
        raise problem(422, "invalid_lines", errors=errors)
    return out


def new_line(
    order: ShopOrder, product: SdProduct, item: LineInput, qty: Decimal, pos: int
) -> ShopOrderLine:
    return ShopOrderLine(
        order_id=order.id,
        product_id=product.id,
        blocks=item.blocks,
        pieces=item.pieces,
        quantity=qty,
        price=Decimal(0),
        line_sum=Decimal(0),
        comment=(item.comment or None),
        position=pos,
    )


async def replace_lines(
    db: AsyncSession,
    order: ShopOrder,
    inputs: list[LineInput],
    *,
    st: LinkSettings | None,
    admin: bool = False,
) -> None:
    prepared = await prepare_lines(db, order, inputs, st=st, admin=admin)
    await db.execute(delete(ShopOrderLine).where(ShopOrderLine.order_id == order.id))
    for pos, (product, item, qty) in enumerate(prepared, start=1):
        db.add(new_line(order, product, item, qty, pos))
    await db.flush()


async def upsert_line(
    db: AsyncSession,
    order: ShopOrder,
    item: LineInput,
    *,
    st: LinkSettings | None,
    admin: bool = False,
) -> None:
    """Add or update one line; zero quantity removes it."""
    existing = await db.scalar(
        select(ShopOrderLine)
        .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
        .where(ShopOrderLine.order_id == order.id, SdProduct.sd_id == item.product_ref)
    )
    if item.blocks == 0 and item.pieces == 0:
        if existing is not None:
            await db.delete(existing)
            await db.flush()
        return
    count = await db.scalar(
        select(func.count()).select_from(ShopOrderLine).where(ShopOrderLine.order_id == order.id)
    )
    if existing is None and (count or 0) >= MAX_LINES:
        raise problem(422, "too_many_lines", message=f"At most {MAX_LINES} lines")
    ((product, _, qty),) = await prepare_lines(db, order, [item], st=st, admin=admin)
    if existing is None:
        pos = (
            await db.scalar(
                select(func.coalesce(func.max(ShopOrderLine.position), 0)).where(
                    ShopOrderLine.order_id == order.id
                )
            )
            or 0
        ) + 1
        db.add(new_line(order, product, item, qty, pos))
    else:
        existing.blocks = item.blocks
        existing.pieces = item.pieces
        existing.quantity = qty
        if item.comment is not None:
            existing.comment = item.comment or None
    await db.flush()


async def remove_line(db: AsyncSession, order: ShopOrder, product_ref: str) -> None:
    line = await db.scalar(
        select(ShopOrderLine)
        .join(SdProduct, SdProduct.id == ShopOrderLine.product_id)
        .where(ShopOrderLine.order_id == order.id, SdProduct.sd_id == product_ref)
    )
    if line is None:
        raise HTTPException(404, "Line not found")
    await db.delete(line)
    await db.flush()


async def touch(
    db: AsyncSession,
    order: ShopOrder,
    *,
    st: LinkSettings | None,
    admin: bool = False,
    credit: dict[int, Decimal] | None = None,
    fixed_prices: dict[int, Decimal] | None = None,
) -> OrderView:
    """After an edit: bump version (guarded), reprice from the mirror and refresh totals."""
    await db.flush()
    await bump_version(db, order)
    view = await build_view(
        db, order, st=st, admin=admin, credit=credit, live=True, fixed_prices=fixed_prices
    )
    await db.flush()
    return view


# --------------------------------------------------------------------------------- validation


def collect_problems(
    view: OrderView,
    options: HeaderOptions,
    client: SdClient,
    *,
    admin: bool,
    check_stock: bool = True,
) -> list[dict[str, Any]]:
    """Everything that blocks sending the order to SD (empty list = ready)."""
    order = view.order
    st = view.st
    out: list[dict[str, Any]] = []
    if not client.active:
        out.append({"code": "client_inactive", "message": "Client is not active"})
    for name, column in HEADER_FIELDS.items():
        value = getattr(order, column)
        if value is None:
            out.append({"field": name, "code": "required", "message": f"{name} is required"})
        elif value not in options.for_field(name):
            out.append({"field": name, "code": ISSUE_NOT_ALLOWED, "message": f"{name} not allowed"})
    if not view.lines:
        out.append({"code": "no_lines", "message": "Add at least one product"})
    for v in view.lines:
        ref = v.product.sd_id
        for issue in v.issues:
            out.append({"product_ref": ref, "code": issue, "message": issue.replace("_", " ")})
        if check_stock and v.over_stock:
            out.append(
                {
                    "product_ref": ref,
                    "code": "over_stock",
                    "message": "Not enough stock",
                    "available": num(v.stock),
                    "requested": num(v.line.quantity),
                }
            )
    total = order.total_sum
    if st is not None and view.lines and not admin:  # admins bypass the link's sum limits
        if st.min_order_sum is not None and total < Decimal(str(st.min_order_sum)):
            out.append(
                {
                    "code": "min_order_sum",
                    "message": "Order sum is below the minimum",
                    "min": st.min_order_sum,
                    "sum": num(total),
                }
            )
        if st.max_order_sum is not None and total > Decimal(str(st.max_order_sum)):
            out.append(
                {
                    "code": "max_order_sum",
                    "message": "Order sum is above the maximum",
                    "max": st.max_order_sum,
                    "sum": num(total),
                }
            )
    return out


# ------------------------------------------------------------------------------ serialization


async def reference_names(
    db: AsyncSession, orders: list[ShopOrder]
) -> dict[str, dict[str, str | None]]:
    return {
        "agent": await _names(db, SdAgent, {o.agent_sd_id for o in orders}),
        "warehouse": await _names(db, SdWarehouse, {o.warehouse_sd_id for o in orders}),
        "price_type": await _names(db, SdPriceType, {o.price_type_sd_id for o in orders}),
        "trade": await _names(db, SdTradeDirection, {o.trade_sd_id for o in orders}),
    }


def header_out(order: ShopOrder, names: dict[str, dict[str, str | None]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, column in HEADER_FIELDS.items():
        value = getattr(order, column)
        out[name] = None if value is None else {"id": value, "name": names[name].get(value)}
    return out


def order_summary(order: ShopOrder, names: dict[str, dict[str, str | None]]) -> dict[str, Any]:
    """Compact row for lists (no lines)."""
    return {
        "public_id": str(order.public_id),
        "code": order.code_1c,
        "state": order.state,
        "sd_status": order.sd_status,
        "sd_id": order.sd_id,
        "version": order.version,
        **header_out(order, names),
        "shipment_date": order.shipment_date,
        "comment": order.comment,
        "total_sum": num(order.total_sum),
        "total_qty": num(order.total_qty),
        "lines_count": order.lines_count,
        "created_by": order.created_by_type,
        "created_at": order.created_at,
        "sent_at": order.sent_at,
        "sd_synced_at": order.sd_synced_at,
        "last_error": order.last_error,
    }


async def order_detail(
    db: AsyncSession,
    view: OrderView,
    options: HeaderOptions | None = None,
    client: SdClient | None = None,
    *,
    admin: bool = False,
) -> dict[str, Any]:
    """Full order document: header, recalculated lines (price/stock/warnings), totals, summary."""
    order = view.order
    names = await reference_names(db, [order])
    pids = [v.product.id for v in view.lines]
    barcodes: dict[int, list[str]] = {}
    if pids:
        for pid, code in (
            await db.execute(
                select(ProductBarcode.product_id, ProductBarcode.barcode).where(
                    ProductBarcode.product_id.in_(pids)
                )
            )
        ).all():
            barcodes.setdefault(pid, []).append(code)
    lines = [
        {
            "product_ref": v.product.sd_id,
            "name": v.product.name,
            "code": v.product.code_1c,
            "category": v.product.category_sd_id,
            "category_name": v.category_name,
            "unit": v.unit_name,
            "image_url": v.product.image_url,
            "thumb_url": v.product.thumb_url,
            "pack_quantity": num(v.product.pack_quantity),
            "barcodes": sorted(barcodes.get(v.product.id, [])),
            "blocks": v.line.blocks,
            "pieces": num(v.line.pieces),
            "quantity": num(v.line.quantity),
            "price": num(v.line.price),
            "line_sum": num(v.line.line_sum),
            "comment": v.line.comment,
            "position": v.line.position,
            "stock": num(v.stock),
            "warnings": v.warnings if view.live else [],
        }
        for v in view.lines
    ]
    st = view.st
    out = {
        **order_summary(order, names),
        "editable": order.state in EDITABLE_STATES,
        "lines": lines,
        "totals": view.totals(),
        "categories": view.categories(),
        "limits": None
        if st is None
        else {
            "min_order_sum": st.min_order_sum,
            "max_order_sum": st.max_order_sum,
            "allow_over_stock": st.allow_over_stock,
        },
    }
    if view.live and options is not None and client is not None:
        out["problems"] = collect_problems(view, options, client, admin=admin)
    return out


async def render_order(
    db: AsyncSession, order: ShopOrder, client: SdClient, *, admin: bool = False
) -> dict[str, Any]:
    """Fresh view of an order as returned by every endpoint (recomputed totals, warnings)."""
    link = await db.get(AccessLink, order.link_id)
    st = parse_settings(link) if link is not None else None
    if admin:
        options = await admin_options(db)
    elif st is not None:
        options = await link_options(db, client, st)
    else:
        options = HeaderOptions()
    view = await build_view(db, order, st=st, admin=admin)
    return await order_detail(db, view, options, client, admin=admin)

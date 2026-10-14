"""Response models shared by the shop/admin APIs (they exist for OpenAPI -> typed clients).

Endpoints keep building plain dicts; ``response_model`` + ``response_model_exclude_unset=True``
validates them against these models without adding or dropping keys, so the JSON shape is the
one the dicts carry (optional keys stay absent when the dict does not set them).
Money / quantities are JSON numbers (``Number``), exactly as ``services.orders.num`` emits them.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

Number = int | float

# keep in sync with app.models.orders.ORDER_STATES (asserted in tests)
OrderState = Literal[
    "draft", "submitting", "sent", "failed", "outcome_unknown", "cancelled", "deleted"
]


class IdName(BaseModel):
    id: str
    name: str | None = None


class OrderLineOut(BaseModel):
    product_ref: str
    name: str | None
    code: str | None
    category: str | None
    category_name: str | None
    unit: str | None
    image_url: str | None
    thumb_url: str | None
    pack_quantity: Number | None
    barcodes: list[str]
    blocks: int
    pieces: Number
    quantity: Number
    price: Number
    line_sum: Number
    comment: str | None
    position: int
    stock: Number | None
    warnings: list[str]


class OrderTotals(BaseModel):
    qty: Number
    sum: Number
    volume: Number
    lines_count: int


class CategorySummary(BaseModel):
    category: str | None
    name: str
    qty: Number
    volume: Number
    sum: Number


class OrderLimits(BaseModel):
    min_order_sum: Number | None
    max_order_sum: Number | None
    allow_over_stock: bool


class OrderProblem(BaseModel):
    """One reason an order cannot be sent (all keys but code/message depend on the code)."""

    code: str
    message: str
    field: str | None = None
    product_ref: str | None = None
    available: Number | None = None
    requested: Number | None = None
    min: Number | None = None
    max: Number | None = None
    sum: Number | None = None


class OrderSummaryOut(BaseModel):
    public_id: str
    code: str
    state: OrderState
    sd_status: int | None
    sd_id: str | None
    version: int
    agent: IdName | None
    warehouse: IdName | None
    price_type: IdName | None
    trade: IdName | None
    shipment_date: date | None
    comment: str
    total_sum: Number
    total_qty: Number
    lines_count: int
    created_by: str
    created_at: datetime
    sent_at: datetime | None
    sd_synced_at: datetime | None
    last_error: dict[str, Any] | None


class OrderOut(OrderSummaryOut):
    """Full recalculated order document (every shop order endpoint, admin detail base)."""

    editable: bool
    lines: list[OrderLineOut]
    totals: OrderTotals
    categories: list[CategorySummary]
    limits: OrderLimits | None
    problems: list[OrderProblem] | None = None  # only for editable (live) orders


class ShopOrderListOut(BaseModel):
    items: list[OrderSummaryOut]
    total: int
    page: int
    size: int


# ------------------------------------------------------------------------------- admin orders


class ClientRef(BaseModel):
    id: int
    name: str | None


class ClientDetailRef(ClientRef):
    sd_id: str


class LinkRef(BaseModel):
    id: int
    slug: str
    name: str


class AdminOrderRow(OrderSummaryOut):
    client: ClientRef
    link: LinkRef


class AdminOrdersSummary(BaseModel):
    count: int
    total_sum: Number
    total_qty: Number


class AdminOrderListOut(BaseModel):
    total: int
    summary: AdminOrdersSummary
    items: list[AdminOrderRow]


class OrderEventOut(BaseModel):
    at: datetime
    actor_type: str
    actor_id: int | None
    event: str
    payload: dict[str, Any] | None


class RepricedLine(BaseModel):
    product_ref: str
    old: Number | None
    new: Number | None


class PriceDriftLine(BaseModel):
    product_ref: str
    kept_price: Number | None
    current_price: Number | None


class AdminOrderOut(OrderOut):
    """Admin order detail. ``repriced``/``price_drift`` only on edit + preview, ``warnings`` only
    on status change, ``reconcile`` only on resend of an outcome_unknown order."""

    client: ClientDetailRef
    link: LinkRef
    created_by_admin_id: int | None
    submitted_at: datetime | None
    events: list[OrderEventOut]
    repriced: list[RepricedLine] | None = None
    price_drift: list[PriceDriftLine] | None = None
    warnings: list[str] | None = None
    reconcile: str | None = None


# --------------------------------------------------------------------------------- catalog


class CatalogItem(BaseModel):
    product_ref: str
    code: str | None
    name: str | None
    category: str | None
    category_name: str | None
    image_url: str | None
    thumb_url: str | None
    pack_quantity: Number | None
    unit: str | None
    price: Number
    stock: Number | None
    barcodes: list[str]


class CatalogOut(BaseModel):
    items: list[CatalogItem]
    total: int
    page: int
    size: int
    price_type: str
    warehouse: str | None


class CategoryOut(BaseModel):
    id: str
    name: str | None
    products: int


class CategoriesOut(BaseModel):
    items: list[CategoryOut]


class BarcodeItemsOut(BaseModel):
    items: list[CatalogItem]


class StockItem(BaseModel):
    product_ref: str
    quantity: Number | None


class StockOut(BaseModel):
    warehouse: str
    fresh: bool
    items: list[StockItem]


# ------------------------------------------------------------------------------------ import


class ImportMatched(CatalogItem):
    rows: list[int]
    blocks: int
    pieces: Number
    quantity: Number


class ImportAmbiguous(BaseModel):
    row: int
    code: str | None
    name: str | None
    blocks: int
    pieces: Number
    candidates: list[CatalogItem]


class ImportUnmatched(BaseModel):
    row: int
    code: str | None
    name: str | None
    reason: str


class ImportPreviewOut(BaseModel):
    rows: int
    matched: list[ImportMatched]
    ambiguous: list[ImportAmbiguous]
    unmatched: list[ImportUnmatched]

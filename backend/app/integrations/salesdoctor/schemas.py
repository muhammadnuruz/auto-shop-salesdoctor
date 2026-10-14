"""Lenient pydantic models for Sales Doctor payloads (SD adds fields freely)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.integrations.salesdoctor.errors import SDRowError


def _to_str(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


def _to_bool(v: Any) -> bool | None:
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().upper() in {"Y", "YES", "1", "TRUE"}
    return bool(v)


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


SDId = Annotated[str | None, BeforeValidator(_to_str)]
SDBool = Annotated[bool | None, BeforeValidator(_to_bool)]
SDNumber = Annotated[float | None, BeforeValidator(_to_float)]


class SDModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class EntityID(SDModel):
    """Reference to an SD entity; values may be null, "" or ints."""

    CS_id: str | int | None = None
    SD_id: str | int | None = None
    code_1C: str | int | None = None

    @property
    def sd_id(self) -> str | None:
        return None if self.SD_id in (None, "") else str(self.SD_id)


class Pagination(SDModel):
    limit: int | None = None
    total: int | None = None
    page: int | None = None


class Reference(SDModel):
    """Common shape of simple reference rows (warehouse, unit, brand, ...)."""

    CS_id: SDId = None
    SD_id: SDId = None
    code_1C: SDId = None
    name: str | None = None
    active: SDBool = None


class Warehouse(Reference):
    pass


class PriceType(Reference):
    type: str | None = None  # "sale" | "purchase" | ...


class TradeDirection(Reference):
    desc: str | None = None


class PaymentType(Reference):
    short: str | None = None


class Unit(Reference):
    short: str | None = None


class ProductCategory(Reference):
    pass


class Brand(Reference):
    pass


class Agent(Reference):
    # live: a list of users; docs: a single object
    user: Any = None

    @property
    def users(self) -> list[dict[str, Any]]:
        if isinstance(self.user, list):
            return [u for u in self.user if isinstance(u, dict)]
        if isinstance(self.user, dict):
            return [self.user]
        return []


class Product(Reference):
    barCode: str | None = None
    packQuantity: SDNumber = None
    imageID: SDId = None
    category: EntityID | None = None
    subCategory: EntityID | None = None
    unit: EntityID | None = None
    brand: EntityID | None = None
    trade: EntityID | None = None


class ClientAgent(SDModel):
    id: SDId = None
    code: SDId = None
    days: Any = None


class Client(Reference):
    firmName: str | None = None
    address: str | None = None
    tel: str | None = None
    agents: list[ClientAgent] = Field(default_factory=list)
    category: EntityID | None = None
    type: EntityID | None = None
    channel: EntityID | None = None
    city: EntityID | None = None
    expeditor: EntityID | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class StockProduct(SDModel):
    SD_id: SDId = None
    CS_id: SDId = None
    code_1C: SDId = None
    name: str | None = None
    active: SDBool = None
    quantity: SDNumber = None


class StockWarehouse(Reference):
    products: list[StockProduct] = Field(default_factory=list)


class PriceRow(SDModel):
    product: EntityID
    price: SDNumber = None


class CurrencyBalance(SDModel):
    currency_id: SDId = None
    amount: SDNumber = None


class BalanceItem(SDModel):
    CS_id: SDId = None
    SD_id: SDId = None
    code_1C: SDId = None
    name: str | None = None
    active: SDBool = None
    balance: SDNumber = None
    by_currency: list[CurrencyBalance] = Field(default_factory=list, alias="by-currency")


# --- orders -----------------------------------------------------------------------------------


class OrderProductIn(SDModel):
    product: EntityID
    quantity: float
    price: float
    discountSumma: float = 0


class OrderIn(SDModel):
    CS_id: str = ""
    SD_id: str = ""
    code_1C: str = Field(min_length=1)
    comment: str = ""
    status: int = 1
    client: EntityID
    agent: EntityID
    priceType: EntityID
    warehouse: EntityID
    trade: EntityID
    orderProducts: list[OrderProductIn]
    dateDocument: str | None = None
    auto_attach_expeditor: bool | None = None


class OrderStatusIn(SDModel):
    CS_id: str = ""
    SD_id: str = ""
    code_1C: str = ""
    status: int
    dateShipment: str | None = None


class Order(SDModel):
    CS_id: SDId = None
    SD_id: SDId = None
    code_1C: SDId = None
    status: int | None = None
    dateCreate: str | None = None
    comment: str | None = None
    client: EntityID | None = None
    orderProducts: list[dict[str, Any]] = Field(default_factory=list)


class SetItemResult(SDModel):
    CS_id: SDId = None
    SD_id: SDId = None
    code_1C: SDId = None


class SetResult(SDModel):
    """Parsed result of a successful set* call."""

    completed: int = 0
    error: int = 0
    items: dict[str, list[SetItemResult]] = Field(default_factory=dict)
    row_errors: list[SDRowError] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only if nothing failed (no error count and no per-row errors)."""
        return self.error == 0 and not self.row_errors

    def ids(self, entity: str) -> list[SetItemResult]:
        return self.items.get(entity, [])


class OrderStatus:
    NEW = 1
    SHIPPED = 2
    DELIVERED = 3
    RETURNED = 4
    CANCELLED = 5

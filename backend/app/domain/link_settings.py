"""Access-link settings (stored as JSONB) and their validation against the SD mirror."""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    SdAgent,
    SdBrand,
    SdPriceType,
    SdProduct,
    SdProductCategory,
    SdTradeDirection,
    SdWarehouse,
)

PickMode = Literal["fixed", "choice"]
AgentMode = Literal["fixed", "choice", "client_assigned"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChoiceSetting(_Strict):
    mode: PickMode = "choice"
    ids: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids must be unique")
        if self.mode == "fixed" and len(self.ids) != 1:
            raise ValueError("mode 'fixed' requires exactly one id")
        if self.mode == "choice" and not self.ids:
            raise ValueError("mode 'choice' requires at least one id")
        return self


class AgentSetting(_Strict):
    mode: AgentMode = "client_assigned"
    ids: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("ids must be unique")
        if self.mode == "fixed" and len(self.ids) != 1:
            raise ValueError("mode 'fixed' requires exactly one id")
        if self.mode == "choice" and not self.ids:
            raise ValueError("mode 'choice' requires at least one id")
        return self


class ProductFilter(_Strict):
    mode: Literal["all", "categories", "brands", "products"] = "all"
    ids: list[str] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.mode != "all" and not self.ids:
            raise ValueError(f"mode '{self.mode}' requires ids")
        return self


class Branding(_Strict):
    title: str | None = Field(None, max_length=100)
    logo_url: str | None = Field(None, max_length=500, pattern=r"^(https://|/)[^\s]*$")
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    welcome_text: str | None = Field(None, max_length=500)


class LinkSettings(_Strict):
    warehouses: ChoiceSetting
    price_types: ChoiceSetting
    agents: AgentSetting = Field(default_factory=AgentSetting)
    trade_directions: ChoiceSetting
    order_status: Literal[1, 2] = 1
    min_order_sum: float | None = Field(None, ge=0)
    max_order_sum: float | None = Field(None, ge=0)
    allow_over_stock: bool = False
    product_filter: ProductFilter = Field(default_factory=ProductFilter)
    branding: Branding = Field(default_factory=Branding)

    @model_validator(mode="after")
    def _check(self) -> Self:
        lo, hi = self.min_order_sum, self.max_order_sum
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("min_order_sum must be <= max_order_sum")
        return self


async def _missing(db: AsyncSession, model: Any, ids: list[str], *extra: Any) -> list[str]:
    if not ids:
        return []
    stmt = select(model.sd_id).where(model.sd_id.in_(ids), model.active.is_(True), *extra)
    found = set((await db.scalars(stmt)).all())
    return [i for i in ids if i not in found]


async def validate_link_settings(db: AsyncSession, s: LinkSettings) -> list[str]:
    """Check that every referenced SD id exists and is active (price types: kind='sale').

    Returns human-readable problems (empty list = valid).
    """
    problems: list[str] = []
    checks: list[tuple[str, Any, list[str], tuple[Any, ...]]] = [
        ("warehouses", SdWarehouse, s.warehouses.ids, ()),
        ("price_types", SdPriceType, s.price_types.ids, (SdPriceType.kind == "sale",)),
        ("agents", SdAgent, s.agents.ids, ()),
        ("trade_directions", SdTradeDirection, s.trade_directions.ids, ()),
    ]
    pf = s.product_filter
    if pf.mode == "categories":
        checks.append(("product_filter", SdProductCategory, pf.ids, ()))
    elif pf.mode == "brands":
        checks.append(("product_filter", SdBrand, pf.ids, ()))
    elif pf.mode == "products":
        checks.append(("product_filter", SdProduct, pf.ids, ()))
    for field, model, ids, where in checks:
        for bad in await _missing(db, model, ids, *where):
            what = (
                "unknown, inactive or non-sale" if field == "price_types" else "unknown or inactive"
            )
            problems.append(f"{field}: '{bad}' is {what}")
    return problems

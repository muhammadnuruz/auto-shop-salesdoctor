"""Pure order rules: product orderability under a link's product filter."""

from decimal import Decimal

from sqlalchemy import ColumnElement, false, or_, true

from app.domain.link_settings import ProductFilter
from app.models import SdProduct

ISSUE_NOT_ALLOWED = "not_allowed"
ISSUE_NO_PRICE = "no_price"


def matches_filter(product: SdProduct, flt: ProductFilter | None) -> bool:
    if flt is None or flt.mode == "all":
        return True
    ids = set(flt.ids)
    if flt.mode == "categories":
        return product.category_sd_id in ids or product.sub_category_sd_id in ids
    if flt.mode == "brands":
        return product.brand_sd_id in ids
    return product.sd_id in ids


def filter_condition(flt: ProductFilter | None) -> ColumnElement[bool]:
    """SQL twin of ``matches_filter``."""
    if flt is None or flt.mode == "all":
        return true()
    if flt.mode == "categories":
        return or_(SdProduct.category_sd_id.in_(flt.ids), SdProduct.sub_category_sd_id.in_(flt.ids))
    if flt.mode == "brands":
        return SdProduct.brand_sd_id.in_(flt.ids)
    if flt.mode == "products":
        return SdProduct.sd_id.in_(flt.ids)
    return false()


def product_issues(
    product: SdProduct,
    *,
    hidden: bool,
    price: Decimal | None,
    flt: ProductFilter | None,
) -> list[str]:
    """Why a product cannot be ordered ([] = orderable). Mirrors the catalog SQL conditions."""
    issues: list[str] = []
    if not product.active or hidden or not matches_filter(product, flt):
        issues.append(ISSUE_NOT_ALLOWED)
    if price is None or price <= 0:
        issues.append(ISSUE_NO_PRICE)
    return issues

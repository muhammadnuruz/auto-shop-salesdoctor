from decimal import Decimal as D

import pytest

from app.domain.link_settings import ProductFilter
from app.domain.order_rules import ISSUE_NO_PRICE, ISSUE_NOT_ALLOWED, matches_filter, product_issues
from app.domain.quantity import QuantityError, compute_quantity, has_pack, line_sum
from app.models import SdProduct


def product(**kw: object) -> SdProduct:
    base: dict[str, object] = {"sd_id": "p", "active": True}
    return SdProduct(**{**base, **kw})


@pytest.mark.parametrize(
    ("blocks", "pieces", "pack", "expected"),
    [
        (0, "1", None, "1"),
        (0, "5", "12", "5"),
        (2, "3", "12", "27"),
        (1, "0", "10", "10"),
        (0, "0.5", None, "0.500"),
        (3, "0", "0.5", "1.500"),
    ],
)
def test_compute_quantity(blocks: int, pieces: str, pack: str | None, expected: str) -> None:
    assert compute_quantity(blocks, D(pieces), None if pack is None else D(pack)) == D(expected)


@pytest.mark.parametrize("pack", [None, D(0)])
def test_blocks_need_a_pack(pack: D | None) -> None:
    assert not has_pack(pack)
    with pytest.raises(QuantityError) as exc:
        compute_quantity(1, D(0), pack)
    assert exc.value.code == "blocks_not_allowed"


@pytest.mark.parametrize(
    ("blocks", "pieces", "code"),
    [
        (-1, "0", "negative_quantity"),
        (0, "-1", "negative_quantity"),
        (0, "1.0001", "bad_pieces"),
        (0, "NaN", "bad_pieces"),
        (10**6, "0", "quantity_too_large"),
        (0, "99999999", "quantity_too_large"),
    ],
)
def test_invalid_quantities(blocks: int, pieces: str, code: str) -> None:
    with pytest.raises(QuantityError) as exc:
        compute_quantity(blocks, D(pieces), D(12))
    assert exc.value.code == code


def test_line_sum_rounds_half_up_to_money() -> None:
    assert line_sum(D("3"), D("0.335")) == D("1.01")
    assert line_sum(D("27"), D("1000")) == D("27000.00")


def test_matches_filter_modes() -> None:
    p = product(sd_id="x", category_sd_id="c1", sub_category_sd_id="c9", brand_sd_id="b1")
    assert matches_filter(p, None) and matches_filter(p, ProductFilter())
    assert matches_filter(p, ProductFilter(mode="categories", ids=["c9"]))
    assert not matches_filter(p, ProductFilter(mode="categories", ids=["c2"]))
    assert matches_filter(p, ProductFilter(mode="brands", ids=["b1"]))
    assert not matches_filter(p, ProductFilter(mode="brands", ids=["b2"]))
    assert matches_filter(p, ProductFilter(mode="products", ids=["x"]))
    assert not matches_filter(p, ProductFilter(mode="products", ids=["y"]))


def test_product_issues() -> None:
    ok = product()
    assert product_issues(ok, hidden=False, price=D(1), flt=None) == []
    assert product_issues(ok, hidden=False, price=None, flt=None) == [ISSUE_NO_PRICE]
    assert product_issues(ok, hidden=False, price=D(0), flt=None) == [ISSUE_NO_PRICE]
    assert product_issues(ok, hidden=True, price=D(1), flt=None) == [ISSUE_NOT_ALLOWED]
    off = product(active=False)
    assert product_issues(off, hidden=False, price=None, flt=None) == [
        ISSUE_NOT_ALLOWED,
        ISSUE_NO_PRICE,
    ]

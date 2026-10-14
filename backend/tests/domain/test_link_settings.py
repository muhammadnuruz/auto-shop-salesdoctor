from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.link_settings import LinkSettings, validate_link_settings
from tests.api.helpers import DEFAULT_SETTINGS, Seed


def build(**over: Any) -> LinkSettings:
    return LinkSettings.model_validate({**DEFAULT_SETTINGS, **over})


def test_defaults() -> None:
    s = build()
    assert s.order_status == 1 and s.allow_over_stock is False
    assert s.product_filter.mode == "all" and s.branding.title is None
    assert s.agents.mode == "client_assigned"


def test_roundtrip_json() -> None:
    s = build(order_status=2, min_order_sum=1, max_order_sum=5)
    assert LinkSettings.model_validate(s.model_dump(mode="json")) == s


@pytest.mark.parametrize(
    "bad",
    [
        {"warehouses": {"mode": "fixed", "ids": []}},
        {"warehouses": {"mode": "fixed", "ids": ["a", "b"]}},
        {"trade_directions": {"mode": "choice", "ids": []}},
        {"agents": {"mode": "choice", "ids": []}},
        {"order_status": 0},
        {"max_order_sum": 1, "min_order_sum": 2},
        {"product_filter": {"mode": "products", "ids": []}},
    ],
)
def test_invalid(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        build(**bad)


async def test_validate_reports_each_bad_reference(session: AsyncSession) -> None:
    seed = Seed(session)
    await seed.reference_data()
    ok = build(price_types={"mode": "choice", "ids": ["d0_2"]})
    assert await validate_link_settings(session, ok) == []
    bad = build(
        warehouses={"mode": "fixed", "ids": ["nope"]},
        price_types={"mode": "choice", "ids": ["d0_2", "d0_7"]},
        agents={"mode": "choice", "ids": ["zzz"]},
        trade_directions={"mode": "choice", "ids": ["404"]},
        product_filter={"mode": "brands", "ids": ["nobrand"]},
    )
    problems = await validate_link_settings(session, bad)
    assert len(problems) == 5
    assert any("price_types: 'd0_7'" in p for p in problems)
    assert any(p.startswith("warehouses: 'nope'") for p in problems)

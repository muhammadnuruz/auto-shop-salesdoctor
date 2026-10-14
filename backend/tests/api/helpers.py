"""Seed helpers for API tests (data is committed into the per-test rolled-back transaction)."""

import itertools
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.security import hash_password
from app.domain.phone import evaluate_phone
from app.models import (
    AccessLink,
    AdminUser,
    ClientPhone,
    PhoneFlag,
    SdAgent,
    SdBrand,
    SdClient,
    SdPriceType,
    SdProduct,
    SdProductCategory,
    SdTradeDirection,
    SdWarehouse,
)

_seq = itertools.count(1)

DEFAULT_SETTINGS: dict[str, Any] = {
    "warehouses": {"mode": "fixed", "ids": ["d0_1"]},
    "price_types": {"mode": "choice", "ids": ["d0_2", "d0_4"]},
    "agents": {"mode": "client_assigned", "ids": []},
    "trade_directions": {"mode": "choice", "ids": ["1"]},
}


class Seed:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def reference_data(self) -> None:
        now = clock.utcnow()
        common = {"active": True, "raw": {}, "synced_at": now}
        s = self.s
        s.add_all(
            [
                SdWarehouse(sd_id="d0_1", name="Основной склад", **common),
                SdWarehouse(sd_id="d0_9", name="Old склад", **{**common, "active": False}),
                SdPriceType(sd_id="d0_2", name="НАЛ", kind="sale", **common),
                SdPriceType(sd_id="d0_4", name="А-ЦЕНА", kind="sale", **common),
                SdPriceType(sd_id="d0_7", name="Приходная цена", kind="purchase", **common),
                SdAgent(sd_id="d0_3", name="MANSUR", **common),
                SdAgent(sd_id="d0_11", name="AGENT11", **common),
                SdTradeDirection(sd_id="1", name="Parfum Brend", **common),
                SdProductCategory(sd_id="c1", name="Cat 1", **common),
                SdBrand(sd_id="b1", name="Brand 1", **common),
                SdProduct(sd_id="p1", name="Prod 1", **common),
            ]
        )
        await s.commit()

    async def admin(
        self,
        username: str = "root",
        *,
        role: str = "superadmin",
        password: str = "correct horse battery",
        extra: list[str] | None = None,
        **kw: Any,
    ) -> AdminUser:
        user = AdminUser(
            username=username,
            full_name=username.title(),
            password_hash=hash_password(password),
            role=role,
            extra_permissions=extra or [],
            **kw,
        )
        self.s.add(user)
        await self.s.commit()
        return user

    async def client(
        self, name: str = "Shop", phones: list[str] | None = None, **kw: Any
    ) -> SdClient:
        n = next(_seq)
        now = clock.utcnow()
        client = SdClient(
            sd_id=kw.pop("sd_id", f"w{n}"),
            name=name,
            address="addr",
            active=kw.pop("active", True),
            raw={},
            synced_at=now,
            agents=kw.pop("agents", [{"id": "d0_11"}]),
            **kw,
        )
        self.s.add(client)
        await self.s.flush()
        for key in phones or []:
            self.s.add(ClientPhone(client_id=client.id, phone_key=key, source="sd"))
        await self.s.commit()
        return client

    async def flag(self, key: str, status: str, count: int = 1) -> None:
        self.s.add(
            PhoneFlag(phone_key=key, status=status, client_count=count, updated_at=clock.utcnow())
        )
        await self.s.commit()

    async def auto_flag(self, key: str, count: int, max_clients: int = 3) -> None:
        status, reason = evaluate_phone(key, count, max_clients)
        self.s.add(
            PhoneFlag(
                phone_key=key,
                status=status,
                reason=reason,
                client_count=count,
                updated_at=clock.utcnow(),
            )
        )
        await self.s.commit()

    async def link(
        self,
        admin: AdminUser,
        slug: str = "shop-link",
        *,
        settings: dict[str, Any] | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        **kw: Any,
    ) -> AccessLink:
        link = AccessLink(
            slug=slug,
            name=kw.pop("name", "Test link"),
            settings=settings or DEFAULT_SETTINGS,
            created_by=admin.id,
            starts_at=starts_at,
            ends_at=ends_at,
            **kw,
        )
        self.s.add(link)
        await self.s.commit()
        return link


async def keys(redis: Any, pattern: str) -> list[str]:
    return [k async for k in redis.scan_iter(pattern)]

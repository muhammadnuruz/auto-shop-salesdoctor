from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import sync as sync_api
from app.models import AuditLog
from app.sync.service import SyncResult
from tests.api.conftest import Api
from tests.api.helpers import Seed


@pytest.fixture
def fake_sync(api: Api, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def run(_ctx: object, entity: str) -> SyncResult:
        calls.append(entity)
        return SyncResult(entity, "ok", items=3, run_id=7)

    async def status(_ctx: object) -> dict[str, Any]:
        return {"runs": ["x"]}

    monkeypatch.setattr(sync_api, "run_sync", run)
    monkeypatch.setattr(sync_api, "latest_runs", status)
    api.http._transport.app.state.sync_ctx = object()  # type: ignore[attr-defined]
    return calls


async def test_internal_sync_routes_are_gone(api: Api) -> None:
    assert (await api.get("/internal/sync/status")).status_code == 404
    assert (await api.post("/internal/sync/clients", csrf=False)).status_code == 404


async def test_sync_requires_auth(api: Api, fake_sync: list[str]) -> None:
    assert (await api.get("/api/admin/sync/status")).status_code == 401
    assert (await api.post("/api/admin/sync/clients", csrf=False)).status_code == 401
    assert fake_sync == []


async def test_status_and_run_for_permitted_admin(
    api: Api, seed: Seed, fake_sync: list[str], session: AsyncSession
) -> None:
    admin = await seed.admin("adm", role="admin")
    await api.admin_login("adm")
    r = await api.get("/api/admin/sync/status")
    assert r.status_code == 200 and "products" in r.json()["entities"] and r.json()["runs"] == ["x"]
    r = await api.post("/api/admin/sync/products")
    assert r.status_code == 200 and r.json()["status"] == "ok" and fake_sync == ["products"]
    a = (await session.scalars(select(AuditLog).where(AuditLog.action == "sync.run"))).one()
    assert a.actor_id == admin.id and a.entity_id == "products"
    assert (await api.post("/api/admin/sync/nope")).status_code == 404


async def test_run_needs_sync_run_but_status_only_sync_read(
    api: Api, seed: Seed, fake_sync: list[str]
) -> None:
    await seed.admin("v", role="viewer")  # sync.read, no sync.run
    await api.admin_login("v")
    assert (await api.get("/api/admin/sync/status")).status_code == 200
    assert (await api.post("/api/admin/sync/products")).status_code == 403
    assert fake_sync == []


async def test_run_requires_csrf(api: Api, seed: Seed, fake_sync: list[str]) -> None:
    await seed.admin("adm", role="admin")
    await api.admin_login("adm")
    assert (await api.post("/api/admin/sync/products", csrf=False)).status_code == 403
    assert fake_sync == []

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AccessLink,
    AdminUser,
    AuditLog,
    ClientSetting,
    PhoneFlag,
    ShopSession,
)
from tests.api.conftest import Api, ApiFactory
from tests.api.helpers import Seed

PHONE = "998902996625"
KEY = "902996625"
GENERIC = "Bu raqam bilan do'kon topilmadi yoki ruxsat yo'q"


@pytest.fixture
async def root(api: Api, seed: Seed) -> AdminUser:
    user = await seed.admin("root")
    await api.admin_login("root")
    return user


@pytest.fixture
async def link(seed: Seed, root: AdminUser) -> AccessLink:
    return await seed.link(root)


async def test_block_client_denies_login_and_kills_sessions(
    api: Api,
    root: AdminUser,
    seed: Seed,
    link: AccessLink,
    session: AsyncSession,
) -> None:
    c = await seed.client("Shop", [KEY])
    shop = Api(api.http)
    assert (await shop.shop_login("shop-link", PHONE)).status_code == 200
    r = await api.post(f"/api/admin/clients/{c.id}/block", {"reason": "debt"})
    assert (
        r.status_code == 200 and r.json()["blocked"] is True and r.json()["revoked_sessions"] == 1
    )
    assert (await shop.get("/api/shop/me")).status_code == 401
    denied = await Api(api.http).shop_login("shop-link", PHONE)
    assert denied.status_code == 401 and denied.json()["detail"] == GENERIC
    setting = await session.get(ClientSetting, c.id)
    assert setting is not None and setting.blocked and setting.blocked_reason == "debt"
    assert setting.updated_by == root.id
    a = (await session.scalars(select(AuditLog).where(AuditLog.action == "client.block"))).one()
    assert a.entity_id == str(c.id) and a.after == {"blocked": True, "reason": "debt"}


async def test_unblock_restores_access(
    api: Api, root: AdminUser, seed: Seed, link: AccessLink, session: AsyncSession
) -> None:
    c = await seed.client("Shop", [KEY])
    await api.post(f"/api/admin/clients/{c.id}/block", {"reason": "x"})
    r = await api.post(f"/api/admin/clients/{c.id}/unblock")
    assert r.json()["blocked"] is False
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.action.like("client.%")))
    ).all()
    assert {"client.block", "client.unblock"} <= set(actions)


async def test_block_unknown_client_404(api: Api, root: AdminUser) -> None:
    assert (await api.post("/api/admin/clients/999999/block", {})).status_code == 404
    assert (await api.post("/api/admin/clients/999999/revoke-sessions")).status_code == 404


async def test_revoke_client_sessions(
    api: Api, root: AdminUser, seed: Seed, link: AccessLink, session: AsyncSession
) -> None:
    c = await seed.client("Shop", [KEY])
    a, b = Api(api.http), Api(api.http)
    await a.shop_login("shop-link", PHONE)
    await b.shop_login("shop-link", PHONE)
    r = await api.post(f"/api/admin/clients/{c.id}/revoke-sessions")
    assert r.json() == {"revoked": 2}
    assert (await a.get("/api/shop/me")).status_code == 401
    # revoking does not block: the shop can log in again
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    rows = (await session.scalars(select(ShopSession.revoked_at))).all()
    assert sum(1 for x in rows if x) == 2
    assert "client.revoke_sessions" in (await session.scalars(select(AuditLog.action))).all()


async def test_phone_override_allowed_admits_suspicious_phone(
    api: Api, root: AdminUser, seed: Seed, link: AccessLink, session: AsyncSession
) -> None:
    await seed.client("Shop", [KEY])
    await seed.flag(KEY, "suspicious")
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 401
    r = await api.put(
        f"/api/admin/phones/{KEY}", {"status": "allowed", "reason": "verified by phone call"}
    )
    assert r.status_code == 200 and r.json()["status"] == "allowed"
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    a = (await session.scalars(select(AuditLog).where(AuditLog.action == "phone.override"))).one()
    assert a.before["status"] == "suspicious" and a.after["status"] == "allowed"  # type: ignore[index]


async def test_phone_override_blocked_revokes_sessions(
    api: Api, root: AdminUser, seed: Seed, link: AccessLink
) -> None:
    await seed.client("Shop", [KEY])
    shop = Api(api.http)
    await shop.shop_login("shop-link", PHONE)
    r = await api.put(f"/api/admin/phones/{KEY}", {"status": "blocked", "reason": "fake"})
    assert r.json()["revoked_sessions"] == 1
    assert (await shop.get("/api/shop/me")).status_code == 401
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 401


async def test_phone_override_auto_reevaluates(
    api: Api, root: AdminUser, seed: Seed, session: AsyncSession
) -> None:
    await seed.client("A", ["999999999"])
    await seed.flag("999999999", "allowed")
    r = await api.put("/api/admin/phones/999999999", {"status": "auto"})
    assert r.json()["status"] == "suspicious" and "repeated_digits" in r.json()["reason"]
    await seed.client("B", [KEY])
    await seed.flag(KEY, "blocked")
    r = await api.put(f"/api/admin/phones/{KEY}", {"status": "auto"})
    assert r.json()["status"] == "ok" and r.json()["reason"] is None
    flag = await session.get(PhoneFlag, KEY)
    assert flag is not None and flag.status == "ok" and flag.client_count == 1


async def test_phone_override_validation(api: Api, root: AdminUser) -> None:
    assert (await api.put("/api/admin/phones/123", {"status": "allowed"})).status_code == 422
    assert (await api.put(f"/api/admin/phones/{KEY}", {"status": "ok"})).status_code == 422
    assert (await api.put(f"/api/admin/phones/{KEY}", {"status": "weird"})).status_code == 422


async def test_list_phones_and_clients(api: Api, root: AdminUser, seed: Seed) -> None:
    c = await seed.client("Тест Дилшод", [KEY], sd_id="w3_7448")
    await seed.client("Other", ["905551122"])
    await seed.flag(KEY, "suspicious", 5)
    await seed.flag("905551122", "ok", 1)
    phones = (await api.get("/api/admin/phones", params={"status": "suspicious"})).json()
    assert phones["total"] == 1 and phones["items"][0]["phone_key"] == KEY
    assert (await api.get("/api/admin/phones", params={"q": "9055"})).json()["total"] == 1
    assert (await api.get("/api/admin/phones", params={"q": "abc"})).status_code == 422
    by_name = (await api.get("/api/admin/clients", params={"q": "Дилшод"})).json()
    assert [i["id"] for i in by_name["items"]] == [c.id]
    by_phone = (await api.get("/api/admin/clients", params={"q": "+998 90 299 66 25"})).json()
    assert [i["id"] for i in by_phone["items"]] == [c.id]
    by_sd = (await api.get("/api/admin/clients", params={"q": "w3_7448"})).json()
    assert by_sd["total"] == 1
    await api.post(f"/api/admin/clients/{c.id}/block", {})
    assert (await api.get("/api/admin/clients", params={"blocked": "true"})).json()["total"] == 1
    assert (await api.get("/api/admin/clients", params={"blocked": "false"})).json()["total"] == 1


async def test_client_admin_requires_clients_write(
    app_factory: ApiFactory, root: AdminUser, seed: Seed
) -> None:
    c = await seed.client("Shop", [KEY])
    await seed.admin("op", role="operator")  # clients.read only
    op = await app_factory()
    await op.admin_login("op")
    assert (await op.get("/api/admin/clients")).status_code == 200
    assert (await op.post(f"/api/admin/clients/{c.id}/block", {})).status_code == 403
    assert (await op.post(f"/api/admin/clients/{c.id}/unblock")).status_code == 403
    assert (await op.post(f"/api/admin/clients/{c.id}/revoke-sessions")).status_code == 403
    assert (await op.put(f"/api/admin/phones/{KEY}", {"status": "blocked"})).status_code == 403

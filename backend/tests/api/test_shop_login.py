import time
from datetime import timedelta
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.security import read_client_ref, sign_client_ref
from app.models import (
    AccessLink,
    AdminUser,
    AuditLog,
    ClientSetting,
    LinkClient,
    ShopSession,
)
from tests.api.conftest import Api, ApiFactory, make_settings
from tests.api.helpers import DEFAULT_SETTINGS, Seed, keys

PHONE = "998902996625"
KEY = "902996625"
GENERIC = "Bu raqam bilan do'kon topilmadi yoki ruxsat yo'q"


@pytest.fixture
async def admin(seed: Seed) -> AdminUser:
    return await seed.admin("root")


async def _audits(session: AsyncSession, action: str) -> list[AuditLog]:
    return list((await session.scalars(select(AuditLog).where(AuditLog.action == action))).all())


# ------------------------------------------------------------------------------- link info


async def test_link_info_active_exposes_only_branding(
    api: Api, seed: Seed, admin: AdminUser
) -> None:
    await seed.link(
        admin,
        "bux-markaz",
        settings={
            **DEFAULT_SETTINGS,
            "branding": {"title": "Bux", "color": "#112233", "welcome_text": "Salom"},
        },
    )
    r = await api.get("/api/shop/links/bux-markaz")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "active" and body["title"] == "Bux" and body["color"] == "#112233"
    assert not {"settings", "warehouses", "id", "name", "created_by", "login_count"} & body.keys()


async def test_link_info_unknown_and_archived_404(api: Api, seed: Seed, admin: AdminUser) -> None:
    await seed.link(admin, "old-one", archived_at=clock.utcnow())
    assert (await api.get("/api/shop/links/nope-nope")).status_code == 404
    assert (await api.get("/api/shop/links/old-one")).status_code == 404
    assert (await api.get("/api/shop/links/BAD_SLUG!")).status_code == 404


@pytest.mark.parametrize(
    "kw",
    [
        {"active": False},
        {"ends_at": clock.utcnow() - timedelta(days=1)},
        {"starts_at": clock.utcnow() + timedelta(days=1)},
        {"max_logins": 1, "login_count": 1},
    ],
    ids=["stopped", "expired", "not_started", "max_reached"],
)
async def test_link_info_inactive_is_generic(
    api: Api, seed: Seed, admin: AdminUser, kw: dict[str, Any]
) -> None:
    await seed.link(admin, "lnk-x", **kw)
    r = await api.get("/api/shop/links/lnk-x")
    assert r.status_code == 200 and r.json() == {"slug": "lnk-x", "state": "inactive"}


# --------------------------------------------------------------------------- login: 0/1/many


async def test_login_single_match_creates_session(
    api: Api,
    seed: Seed,
    admin: AdminUser,
    session: AsyncSession,
) -> None:
    client = await seed.client("Тест Дилшод", [KEY])
    link = await seed.link(admin)
    r = await api.shop_login("shop-link", "+998 (90) 299-66-25")
    assert r.status_code == 200 and r.json()["csrf_token"] and r.json()["choose"] is None
    cookie = r.headers["set-cookie"]
    assert "shop_session=" in cookie and "HttpOnly" in cookie and "Path=/api/shop" in cookie
    assert "SameSite=lax" in cookie
    row = (await session.scalars(select(ShopSession))).one()
    assert row.client_id == client.id and row.link_id == link.id and row.phone_key == KEY
    raw_token = api.http.cookies.get("shop_session")
    assert raw_token and row.token_hash != raw_token and len(row.token_hash) == 64
    await session.refresh(link)
    assert link.login_count == 1
    audit = (await _audits(session, "shop.login"))[0]
    assert audit.actor_type == "shop" and audit.actor_id == client.id
    assert audit.request_id and raw_token not in str(audit.after)
    assert PHONE not in str(audit.after) and KEY not in str(audit.after)  # masked


async def test_login_secure_flag_set(app_factory: ApiFactory, seed: Seed, admin: AdminUser) -> None:
    await seed.client("A", [KEY])
    await seed.link(admin)
    api = await app_factory(make_settings(COOKIE_SECURE=True))
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 200 and "Secure" in r.headers["set-cookie"]


async def test_login_no_match_generic_401_and_audit(
    api: Api,
    seed: Seed,
    admin: AdminUser,
    session: AsyncSession,
) -> None:
    await seed.link(admin)
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 401 and r.json()["detail"] == GENERIC
    assert "set-cookie" not in r.headers
    a = (await _audits(session, "shop.login_denied"))[0]
    assert a.actor_type == "system" and a.after == {"reason": "not_found", "phone": "90*****25"}


@pytest.mark.parametrize("phone", ["123", "+99890", "abcdefghi", "", "99890299662599"])
async def test_login_invalid_phone_422(api: Api, seed: Seed, admin: AdminUser, phone: str) -> None:
    await seed.link(admin)
    assert (await api.shop_login("shop-link", phone)).status_code == 422


async def test_login_unknown_link_404_and_inactive_403(
    api: Api, seed: Seed, admin: AdminUser
) -> None:
    await seed.client("A", [KEY])
    await seed.link(admin, "off-link", active=False)
    assert (await api.shop_login("missing-link", PHONE)).status_code == 404
    assert (await api.shop_login("off-link", PHONE)).status_code == 403


async def test_login_multiple_matches_returns_choose_without_session(
    api: Api,
    seed: Seed,
    admin: AdminUser,
    session: AsyncSession,
) -> None:
    a = await seed.client("Alpha", [KEY])
    b = await seed.client("Beta", [KEY])
    await seed.link(admin)
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 200 and r.json()["csrf_token"] is None
    choose = r.json()["choose"]
    assert [c["name"] for c in choose] == ["Alpha", "Beta"]
    assert set(choose[0]) == {"client_ref", "name", "address"}
    assert "set-cookie" not in r.headers
    assert (await session.scalars(select(ShopSession))).all() == []
    refs = {read_client_ref(make_settings(), c["client_ref"], "shop-link") for c in choose}
    assert refs == {(KEY, a.id), (KEY, b.id)}


async def test_select_flow_creates_session_for_chosen_client(
    api: Api,
    seed: Seed,
    admin: AdminUser,
    session: AsyncSession,
) -> None:
    await seed.client("Alpha", [KEY])
    b = await seed.client("Beta", [KEY])
    link = await seed.link(admin)
    choose = (await api.shop_login("shop-link", PHONE)).json()["choose"]
    ref = next(c["client_ref"] for c in choose if c["name"] == "Beta")
    r = await api.post(
        "/api/shop/auth/select", {"slug": "shop-link", "client_ref": ref}, csrf=False
    )
    assert r.status_code == 200 and r.json()["csrf_token"]
    assert (await session.scalars(select(ShopSession))).one().client_id == b.id
    await session.refresh(link)
    assert link.login_count == 1  # counted at select, not at the choose step


async def test_select_rejects_tampered_expired_and_wrong_slug(
    api: Api,
    seed: Seed,
    admin: AdminUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = await seed.client("Alpha", [KEY])
    await seed.client("Beta", [KEY])
    await seed.link(admin)
    await seed.link(admin, "other-link")
    good = sign_client_ref(make_settings(), phone_key=KEY, slug="shop-link", client_id=c.id)

    async def select(slug: str, ref: str) -> int:
        r = await api.post("/api/shop/auth/select", {"slug": slug, "client_ref": ref}, csrf=False)
        return r.status_code

    tampered = good[:-2] + ("aa" if good[-2:] != "aa" else "bb")
    assert await select("shop-link", tampered) == 401
    assert await select("shop-link", "garbage") == 401
    assert await select("other-link", good) == 401  # bound to the slug it was issued for
    # signed with another key
    forged = sign_client_ref(
        make_settings(APP_SECRET_KEY="x" * 40), phone_key=KEY, slug="shop-link", client_id=c.id
    )
    assert await select("shop-link", forged) == 401
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 301)
    assert await select("shop-link", good) == 401  # 5 minute TTL
    monkeypatch.undo()
    assert await select("shop-link", good) == 200


async def test_select_revalidates_eligibility(
    api: Api, seed: Seed, admin: AdminUser, session: AsyncSession
) -> None:
    c = await seed.client("Alpha", [KEY])
    await seed.client("Beta", [KEY])
    await seed.link(admin)
    ref = sign_client_ref(make_settings(), phone_key=KEY, slug="shop-link", client_id=c.id)
    session.add(ClientSetting(client_id=c.id, blocked=True))
    await session.commit()
    r = await api.post(
        "/api/shop/auth/select", {"slug": "shop-link", "client_ref": ref}, csrf=False
    )
    assert r.status_code == 401 and r.json()["detail"] == GENERIC


# ------------------------------------------------------------------ phone flags / client state


@pytest.mark.parametrize("status", ["suspicious", "blocked"])
async def test_flagged_phone_denied_with_generic_message(
    api: Api,
    seed: Seed,
    admin: AdminUser,
    session: AsyncSession,
    status: str,
) -> None:
    await seed.client("A", [KEY])
    await seed.flag(KEY, status)
    await seed.link(admin)
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 401 and r.json()["detail"] == GENERIC  # no enumeration detail
    a = (await _audits(session, "shop.login_denied"))[0]
    assert a.after["reason"] == f"phone_{status}"  # type: ignore[index]


async def test_allowed_override_admits_phone(api: Api, seed: Seed, admin: AdminUser) -> None:
    await seed.client("A", [KEY])
    await seed.flag(KEY, "allowed")
    await seed.link(admin)
    assert (await api.shop_login("shop-link", PHONE)).status_code == 200


async def test_ok_flag_and_missing_flag_admitted(api: Api, seed: Seed, admin: AdminUser) -> None:
    await seed.client("A", [KEY])
    await seed.client("B", ["905551122"])
    await seed.flag("905551122", "ok")
    await seed.link(admin)
    assert (await api.shop_login("shop-link", PHONE)).status_code == 200
    assert (await Api(api.http).shop_login("shop-link", "905551122")).status_code == 200


async def test_blocked_client_denied(
    api: Api, seed: Seed, admin: AdminUser, session: AsyncSession
) -> None:
    c = await seed.client("A", [KEY])
    session.add(ClientSetting(client_id=c.id, blocked=True, blocked_reason="debt"))
    await session.commit()
    await seed.link(admin)
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 401 and r.json()["detail"] == GENERIC


async def test_inactive_client_denied(api: Api, seed: Seed, admin: AdminUser) -> None:
    await seed.client("A", [KEY], active=False)
    await seed.link(admin)
    assert (await api.shop_login("shop-link", PHONE)).status_code == 401


async def test_blocked_client_filtered_out_of_choose_list(
    api: Api, seed: Seed, admin: AdminUser, session: AsyncSession
) -> None:
    await seed.client("Alpha", [KEY])
    b = await seed.client("Beta", [KEY])
    session.add(ClientSetting(client_id=b.id, blocked=True))
    await session.commit()
    await seed.link(admin)
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 200 and r.json()["csrf_token"]  # single eligible -> direct session


# ------------------------------------------------------------------------ link state / quota


async def test_inactive_expired_future_links_refuse_login(
    api: Api, seed: Seed, admin: AdminUser
) -> None:
    await seed.client("A", [KEY])
    now = clock.utcnow()
    await seed.link(admin, "expired-1", ends_at=now - timedelta(minutes=1))
    await seed.link(admin, "future-1", starts_at=now + timedelta(hours=1))
    await seed.link(admin, "paused-1", active=False)
    for slug in ("expired-1", "future-1", "paused-1"):
        r = await api.shop_login(slug, PHONE)
        assert r.status_code == 403 and "set-cookie" not in r.headers


async def test_max_logins_enforced_atomically(
    api: Api, seed: Seed, admin: AdminUser, session: AsyncSession
) -> None:
    await seed.client("A", [KEY])
    link = await seed.link(admin, max_logins=2)
    for _ in range(2):
        assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    r = await Api(api.http).shop_login("shop-link", PHONE)
    assert r.status_code == 403
    await session.refresh(link)
    assert link.login_count == 2
    assert len((await session.scalars(select(ShopSession))).all()) == 2


async def test_max_logins_race_guard_in_update(
    api: Api,
    seed: Seed,
    admin: AdminUser,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if the pre-check passed (stale read), the guarded UPDATE refuses to exceed max."""
    await seed.client("A", [KEY])
    link = await seed.link(admin, max_logins=1)
    monkeypatch.setattr("app.api.shop.link_accepts_logins", lambda *_a, **_k: True)
    await session.execute(update(AccessLink).where(AccessLink.id == link.id).values(login_count=1))
    await session.commit()
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 403
    await session.refresh(link)
    assert link.login_count == 1


# -------------------------------------------------------------------------- client scopes


async def test_scope_all(api: Api, seed: Seed, admin: AdminUser) -> None:
    await seed.client("A", [KEY])
    await seed.link(admin, client_scope="all")
    assert (await api.shop_login("shop-link", PHONE)).status_code == 200


async def test_scope_selected(
    api: Api, seed: Seed, admin: AdminUser, session: AsyncSession
) -> None:
    a = await seed.client("A", [KEY])
    await seed.client("B", ["905551122"])
    link = await seed.link(admin, client_scope="selected")
    session.add(LinkClient(link_id=link.id, client_id=a.id))
    await session.commit()
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    assert (await Api(api.http).shop_login("shop-link", "905551122")).status_code == 401


async def test_scope_territory(
    api: Api, seed: Seed, admin: AdminUser, session: AsyncSession
) -> None:
    await seed.client("A", [KEY], city_sd_id="city1")
    await seed.client("B", ["905551122"], city_sd_id="city2")
    await seed.link(admin, client_scope="territory", scope_values=["city1"])
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    assert (await Api(api.http).shop_login("shop-link", "905551122")).status_code == 401


async def test_scope_category(api: Api, seed: Seed, admin: AdminUser) -> None:
    await seed.client("A", [KEY], category_sd_id="k1")
    await seed.client("B", ["905551122"], category_sd_id="k2")
    await seed.client("C", ["905553344"])
    await seed.link(admin, client_scope="category", scope_values=["k1", "k9"])
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    assert (await Api(api.http).shop_login("shop-link", "905551122")).status_code == 401
    assert (await Api(api.http).shop_login("shop-link", "905553344")).status_code == 401


async def test_scope_filters_choose_list(api: Api, seed: Seed, admin: AdminUser) -> None:
    await seed.client("Alpha", [KEY], city_sd_id="city1")
    await seed.client("Beta", [KEY], city_sd_id="city1")
    await seed.client("Gamma", [KEY], city_sd_id="city2")
    await seed.link(admin, client_scope="territory", scope_values=["city1"])
    r = await api.shop_login("shop-link", PHONE)
    assert [c["name"] for c in r.json()["choose"]] == ["Alpha", "Beta"]


async def test_client_ref_is_single_use(
    api: Api, seed: Seed, admin: AdminUser, session: AsyncSession
) -> None:
    await seed.client("Alpha", [KEY])
    await seed.client("Beta", [KEY])
    await seed.link(admin)
    choose = (await api.shop_login("shop-link", PHONE)).json()["choose"]
    ref = choose[0]["client_ref"]
    body = {"slug": "shop-link", "client_ref": ref}
    assert (await api.post("/api/shop/auth/select", body, csrf=False)).status_code == 200
    again = await Api(api.http).post("/api/shop/auth/select", body, csrf=False)
    assert again.status_code == 401
    assert len((await session.scalars(select(ShopSession))).all()) == 1
    # the other entry from the same choose list is an independent ref and still works
    other = {"slug": "shop-link", "client_ref": choose[1]["client_ref"]}
    assert (await Api(api.http).post("/api/shop/auth/select", other, csrf=False)).status_code == 200


async def test_client_ref_use_key_expires_with_ref(
    api: Api, seed: Seed, admin: AdminUser, redis: FakeAsyncRedis
) -> None:
    await seed.client("Alpha", [KEY])
    await seed.client("Beta", [KEY])
    await seed.link(admin)
    ref = (await api.shop_login("shop-link", PHONE)).json()["choose"][0]["client_ref"]
    await api.post("/api/shop/auth/select", {"slug": "shop-link", "client_ref": ref}, csrf=False)
    key = (await keys(redis, "cref:used:*"))[0]
    assert 0 < await redis.ttl(key) <= 305

import re
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models import AccessLink, AdminUser, AuditLog, LinkClient
from tests.api.conftest import Api, ApiFactory
from tests.api.helpers import DEFAULT_SETTINGS, Seed

URL = "/api/admin/links"
PHONE = "998902996625"


@pytest.fixture
async def root(api: Api, seed: Seed) -> AdminUser:
    user = await seed.admin("root")
    await api.admin_login("root")
    return user


def payload(**kw: Any) -> dict[str, Any]:
    return {"name": "Bux markaz", "settings": DEFAULT_SETTINGS, **kw}


async def test_create_link_auto_slug_and_defaults(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    r = await api.post(URL, payload())
    assert r.status_code == 201
    body = r.json()
    assert re.fullmatch(r"[a-z0-9]{8}", body["slug"])
    assert body["state"] == "active" and body["login_count"] == 0 and body["client_scope"] == "all"
    assert body["created_by"] == root.id
    s = body["settings"]
    assert s["order_status"] == 1 and s["allow_over_stock"] is False
    assert s["product_filter"] == {"mode": "all", "ids": []}
    assert s["warehouses"] == {"mode": "fixed", "ids": ["d0_1"]}
    a = (await session.scalars(select(AuditLog).where(AuditLog.action == "link.create"))).one()
    assert a.entity_id == str(body["id"]) and a.actor_id == root.id


async def test_create_link_custom_slug_rules(api: Api, root: AdminUser) -> None:
    assert (await api.post(URL, payload(slug="bux-markaz"))).json()["slug"] == "bux-markaz"
    assert (await api.post(URL, payload(slug="bux-markaz"))).status_code == 409
    for bad in ("ab", "UPPER", "has space", "a" * 65, "под_слаг"):
        assert (await api.post(URL, payload(slug=bad))).status_code == 422, bad


async def test_two_auto_slugs_differ(api: Api, root: AdminUser) -> None:
    a = (await api.post(URL, payload())).json()["slug"]
    b = (await api.post(URL, payload())).json()["slug"]
    assert a != b


# ---------------------------------------------------------------- settings validation


def settings_with(**over: Any) -> dict[str, Any]:
    return {**DEFAULT_SETTINGS, **over}


async def test_unknown_warehouse_rejected(api: Api, root: AdminUser) -> None:
    r = await api.post(
        URL, payload(settings=settings_with(warehouses={"mode": "fixed", "ids": ["d0_999"]}))
    )
    assert r.status_code == 422 and "warehouses: 'd0_999'" in str(r.json())


async def test_inactive_warehouse_rejected(api: Api, root: AdminUser) -> None:
    r = await api.post(
        URL, payload(settings=settings_with(warehouses={"mode": "fixed", "ids": ["d0_9"]}))
    )
    assert r.status_code == 422


async def test_purchase_price_type_rejected(api: Api, root: AdminUser) -> None:
    r = await api.post(
        URL,
        payload(settings=settings_with(price_types={"mode": "choice", "ids": ["d0_2", "d0_7"]})),
    )
    assert r.status_code == 422 and "'d0_7'" in str(r.json())


@pytest.mark.parametrize(
    "bad",
    [
        {"agents": {"mode": "choice", "ids": ["d0_404"]}},
        {"trade_directions": {"mode": "choice", "ids": ["77"]}},
        {"product_filter": {"mode": "categories", "ids": ["nope"]}},
        {"product_filter": {"mode": "brands", "ids": ["nope"]}},
        {"product_filter": {"mode": "products", "ids": ["nope"]}},
    ],
)
async def test_other_unknown_references_rejected(
    api: Api, root: AdminUser, bad: dict[str, Any]
) -> None:
    assert (await api.post(URL, payload(settings=settings_with(**bad)))).status_code == 422


@pytest.mark.parametrize(
    "good",
    [
        {"agents": {"mode": "choice", "ids": ["d0_3", "d0_11"]}},
        {"agents": {"mode": "fixed", "ids": ["d0_3"]}},
        {"agents": {"mode": "client_assigned"}},
        {"product_filter": {"mode": "categories", "ids": ["c1"]}},
        {"product_filter": {"mode": "brands", "ids": ["b1"]}},
        {"product_filter": {"mode": "products", "ids": ["p1"]}},
        {"order_status": 2, "min_order_sum": 10, "max_order_sum": 100, "allow_over_stock": True},
        {
            "branding": {
                "title": "T",
                "logo_url": "https://x/y.png",
                "color": "#a1b2c3",
                "welcome_text": "hi",
            }
        },
    ],
)
async def test_valid_settings_accepted(api: Api, root: AdminUser, good: dict[str, Any]) -> None:
    r = await api.post(URL, payload(settings=settings_with(**good)))
    assert r.status_code == 201, r.text


@pytest.mark.parametrize(
    "bad",
    [
        {"warehouses": {"mode": "fixed", "ids": ["d0_1", "d0_9"]}},
        {"warehouses": {"mode": "choice", "ids": []}},
        {"warehouses": {"mode": "weird", "ids": ["d0_1"]}},
        {"price_types": {"mode": "choice", "ids": ["d0_2", "d0_2"]}},
        {"agents": {"mode": "fixed", "ids": []}},
        {"order_status": 3},
        {"min_order_sum": 500, "max_order_sum": 100},
        {"min_order_sum": -1},
        {"product_filter": {"mode": "brands", "ids": []}},
        {"branding": {"color": "red"}},
        {"branding": {"logo_url": "javascript:alert(1)"}},
        {"surprise": True},
    ],
)
async def test_malformed_settings_rejected(api: Api, root: AdminUser, bad: dict[str, Any]) -> None:
    assert (await api.post(URL, payload(settings=settings_with(**bad)))).status_code == 422


async def test_settings_required(api: Api, root: AdminUser) -> None:
    assert (await api.post(URL, {"name": "x"})).status_code == 422


async def test_scope_and_period_validation(api: Api, root: AdminUser) -> None:
    assert (
        await api.post(URL, payload(client_scope="territory", scope_values=[]))
    ).status_code == 422
    assert (
        await api.post(URL, payload(client_scope="category", scope_values=[]))
    ).status_code == 422
    ok = await api.post(URL, payload(client_scope="territory", scope_values=["city1"]))
    assert ok.status_code == 201 and ok.json()["scope_values"] == ["city1"]
    now = clock.utcnow()
    r = await api.post(
        URL, payload(starts_at=now.isoformat(), ends_at=(now - timedelta(days=1)).isoformat())
    )
    assert r.status_code == 422
    assert (
        await api.post(URL, payload(starts_at="2026-01-01T00:00:00"))
    ).status_code == 422  # naive
    assert (await api.post(URL, payload(max_logins=0))).status_code == 422


# ------------------------------------------------------------------- list / get / update


async def test_list_filters_and_search(api: Api, root: AdminUser, seed: Seed) -> None:
    a = (await api.post(URL, payload(name="Alpha shop", slug="alpha-1"))).json()
    b = (await api.post(URL, payload(name="Beta shop", slug="beta-1", active=False))).json()
    c = (await api.post(URL, payload(name="Gamma 100%", slug="gamma-1"))).json()
    await api.post(f"{URL}/{c['id']}/archive")

    def ids(r: Any) -> list[int]:
        return [i["id"] for i in r.json()["items"]]

    assert ids(await api.get(URL)) == [b["id"], a["id"]]  # newest first, archived hidden
    assert ids(await api.get(URL, params={"active": "true"})) == [a["id"]]
    assert ids(await api.get(URL, params={"active": "false"})) == [b["id"]]
    assert ids(await api.get(URL, params={"archived": "true"})) == [c["id"]]
    assert ids(await api.get(URL, params={"q": "alpha"})) == [a["id"]]
    assert ids(await api.get(URL, params={"q": "beta-1"})) == [b["id"]]
    assert ids(await api.get(URL, params={"q": "%"})) == []  # wildcards are escaped
    r = await api.get(URL, params={"limit": 1})
    assert r.json()["total"] == 2 and len(r.json()["items"]) == 1


async def test_get_and_404(api: Api, root: AdminUser) -> None:
    link = (await api.post(URL, payload())).json()
    assert (await api.get(f"{URL}/{link['id']}")).json()["slug"] == link["slug"]
    assert (await api.get(f"{URL}/999999")).status_code == 404


async def test_update_link_partial_and_audited(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    link = (await api.post(URL, payload(name="Old", max_logins=5))).json()
    r = await api.patch(f"{URL}/{link['id']}", {"name": "New", "active": False, "max_logins": None})
    body = r.json()
    assert r.status_code == 200 and body["name"] == "New" and body["active"] is False
    assert body["max_logins"] is None and body["slug"] == link["slug"]
    assert body["settings"] == link["settings"]
    a = (await session.scalars(select(AuditLog).where(AuditLog.action == "link.update"))).one()
    assert a.before["name"] == "Old" and a.after["name"] == "New"  # type: ignore[index]


async def test_update_validates_settings_and_period(api: Api, root: AdminUser) -> None:
    link = (await api.post(URL, payload())).json()
    r = await api.patch(
        f"{URL}/{link['id']}",
        {"settings": settings_with(warehouses={"mode": "fixed", "ids": ["zz"]})},
    )
    assert r.status_code == 422
    new = settings_with(order_status=2)
    assert (await api.patch(f"{URL}/{link['id']}", {"settings": new})).json()["settings"][
        "order_status"
    ] == 2
    now = clock.utcnow()
    r = await api.patch(
        f"{URL}/{link['id']}",
        {"starts_at": now.isoformat(), "ends_at": (now - timedelta(hours=1)).isoformat()},
    )
    assert r.status_code == 422
    r = await api.patch(f"{URL}/{link['id']}", {"client_scope": "category"})
    assert r.status_code == 422
    assert (await api.patch(f"{URL}/999999", {"name": "x"})).status_code == 404


async def test_archive_unarchive(
    api: Api, root: AdminUser, seed: Seed, session: AsyncSession
) -> None:
    await seed.client("Shop", [PHONE[3:]])
    link = (await api.post(URL, payload(slug="arch-link"))).json()
    shop = Api(api.http)
    assert (await shop.shop_login("arch-link", PHONE)).status_code == 200
    r = await api.post(f"{URL}/{link['id']}/archive")
    assert r.json()["state"] == "archived" and r.json()["archived_at"]
    assert (await Api(api.http).get("/api/shop/links/arch-link")).status_code == 404
    assert (await shop.get("/api/shop/me")).status_code == 401  # live sessions die with the link
    r = await api.post(f"{URL}/{link['id']}/unarchive")
    assert r.json()["state"] == "active" and r.json()["archived_at"] is None
    actions = (
        await session.scalars(select(AuditLog.action).where(AuditLog.action.like("link.%")))
    ).all()
    assert {"link.archive", "link.unarchive"} <= set(actions)


async def test_clone_copies_settings_and_clients_but_not_counters(
    api: Api, root: AdminUser, seed: Seed, session: AsyncSession
) -> None:
    c1, c2 = await seed.client("A", ["905550001"]), await seed.client("B", ["905550002"])
    src = (
        await api.post(
            URL, payload(name="Src", client_scope="selected", max_logins=9, slug="src-link")
        )
    ).json()
    await api.put(f"{URL}/{src['id']}/clients", {"add": [c1.id, c2.id]})
    await session.execute(
        AccessLink.__table__.update().where(AccessLink.id == src["id"]).values(login_count=4)  # type: ignore[attr-defined]
    )
    await session.commit()
    r = await api.post(f"{URL}/{src['id']}/clone", {})
    assert r.status_code == 201
    clone = r.json()
    assert (
        clone["id"] != src["id"] and clone["slug"] != "src-link" and clone["name"] == "Src (copy)"
    )
    assert clone["settings"] == src["settings"] and clone["max_logins"] == 9
    assert clone["login_count"] == 0 and clone["client_scope"] == "selected"
    rows = (
        await session.scalars(select(LinkClient.client_id).where(LinkClient.link_id == clone["id"]))
    ).all()
    assert sorted(rows) == sorted([c1.id, c2.id])
    named = await api.post(f"{URL}/{src['id']}/clone", {"name": "Custom"})
    assert named.json()["name"] == "Custom"


async def test_clients_bulk_add_remove(api: Api, root: AdminUser, seed: Seed) -> None:
    cs = [await seed.client(f"C{i}", [f"90555000{i}"]) for i in range(3)]
    link = (await api.post(URL, payload(client_scope="selected"))).json()
    u = f"{URL}/{link['id']}/clients"
    r = await api.put(u, {"add": [c.id for c in cs]})
    assert r.json() == {"added": 3, "removed": 0, "total": 3}
    assert (await api.put(u, {"add": [cs[0].id]})).json()["total"] == 3  # idempotent
    r = await api.put(u, {"remove": [cs[0].id, cs[1].id]})
    assert r.json() == {"added": 0, "removed": 2, "total": 1}
    listing = (await api.get(u)).json()
    assert listing["total"] == 1 and listing["items"][0]["name"] == "C2"
    assert (await api.put(u, {"add": [999999]})).status_code == 422
    assert (await api.put(u, {"add": [cs[0].id], "remove": [cs[0].id]})).status_code == 422
    assert (await api.put(f"{URL}/999999/clients", {"add": []})).status_code == 404


async def test_selected_scope_end_to_end(api: Api, root: AdminUser, seed: Seed) -> None:
    c = await seed.client("Тест Дилшод", [PHONE[3:]], sd_id="w3_7448")
    link = (await api.post(URL, payload(slug="sel-link", client_scope="selected"))).json()
    shop = Api(api.http)
    assert (await shop.shop_login("sel-link", PHONE)).status_code == 401
    await api.put(f"{URL}/{link['id']}/clients", {"add": [c.id]})
    assert (await shop.shop_login("sel-link", PHONE)).status_code == 200
    await api.put(f"{URL}/{link['id']}/clients", {"remove": [c.id]})
    assert (await Api(api.http).shop_login("sel-link", PHONE)).status_code == 401


async def test_stats(api: Api, root: AdminUser, seed: Seed) -> None:
    await seed.client("A", [PHONE[3:]])
    link = (await api.post(URL, payload(slug="stat-link", max_logins=10))).json()
    await Api(api.http).shop_login("stat-link", PHONE)
    await Api(api.http).shop_login("stat-link", PHONE)
    s = (await api.get(f"{URL}/{link['id']}/stats")).json()
    assert s["login_count"] == 2 and s["max_logins"] == 10 and s["active_sessions"] == 2


async def test_link_endpoints_require_permissions(
    app_factory: ApiFactory, api: Api, root: AdminUser, seed: Seed
) -> None:
    link = (await api.post(URL, payload())).json()
    await seed.admin("op", role="operator")  # links.read only
    op = await app_factory()
    await op.admin_login("op")
    assert (await op.get(f"{URL}/{link['id']}")).status_code == 200
    cases: list[tuple[str, str, Any]] = [
        ("post", URL, payload()),
        ("patch", f"{URL}/{link['id']}", {"name": "x"}),
        ("post", f"{URL}/{link['id']}/archive", None),
        ("post", f"{URL}/{link['id']}/clone", {}),
        ("put", f"{URL}/{link['id']}/clients", {"add": []}),
    ]
    for method, url, body in cases:
        r = (
            await getattr(op, method)(url, body)
            if body is not None
            else await getattr(op, method)(url)
        )
        assert r.status_code == 403, (method, url)


async def test_created_link_drives_shop_login_and_me(api: Api, root: AdminUser, seed: Seed) -> None:
    """The Phase 3 smoke scenario, as a test."""
    await seed.client("Тест Дилшод", [PHONE[3:]], sd_id="w3_7448", agents=[{"id": "d0_3"}])
    settings = {
        "warehouses": {"mode": "fixed", "ids": ["d0_1"]},
        "price_types": {"mode": "choice", "ids": ["d0_2", "d0_4"]},
        "agents": {"mode": "client_assigned", "ids": []},
        "trade_directions": {"mode": "choice", "ids": ["1"]},
    }
    link = (await api.post(URL, payload(slug="smoke-link", settings=settings))).json()
    shop = Api(api.http)
    info = await shop.get("/api/shop/links/smoke-link")
    assert info.json()["state"] == "active"
    assert (await shop.shop_login(link["slug"], PHONE)).status_code == 200
    me = (await shop.get("/api/shop/me")).json()
    assert me["client"]["name"] == "Тест Дилшод"
    assert me["options"]["agents"]["options"] == [{"id": "d0_3", "name": "MANSUR"}]
    assert me["options"]["warehouses"]["options"][0]["name"] == "Основной склад"


@pytest.mark.parametrize("slug", ["abcde", "a-b-c", "abc", "ab"])
async def test_custom_slug_minimum_length_is_six(api: Api, root: AdminUser, slug: str) -> None:
    assert (await api.post(URL, payload(slug=slug))).status_code == 422


@pytest.mark.parametrize("slug", ["aaaaaa", "123456", "test-shop", "demo-12", "abab-abab"])
async def test_guessable_custom_slug_accepted_with_warning(
    api: Api, root: AdminUser, slug: str
) -> None:
    r = await api.post(URL, payload(slug=slug))
    assert r.status_code == 201 and r.json()["warnings"] == ["slug_low_entropy"]


async def test_strong_slugs_have_no_warning(api: Api, root: AdminUser) -> None:
    for slug in ("bux-markaz", "k8fq2x9d"):
        r = await api.post(URL, payload(slug=slug))
        assert r.status_code == 201 and "warnings" not in r.json()
    auto = await api.post(URL, payload())
    assert "warnings" not in auto.json() and len(auto.json()["slug"]) == 8

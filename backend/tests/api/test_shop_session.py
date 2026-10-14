import logging
from datetime import timedelta

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models import (
    AccessLink,
    AuditLog,
    ClientSetting,
    PhoneFlag,
    SdClient,
    SdClientBalance,
    ShopSession,
)
from app.services import sessions
from tests.api.conftest import ORIGIN, Api, ApiFactory, make_settings
from tests.api.helpers import DEFAULT_SETTINGS, Seed, keys

PHONE = "998902996625"
KEY = "902996625"


@pytest.fixture
async def shop(api: Api, seed: Seed) -> tuple[SdClient, AccessLink]:
    admin = await seed.admin("root")
    client = await seed.client("Тест Дилшод", [KEY], sd_id="w3_7448")
    link = await seed.link(admin)
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 200
    return client, link


# ---------------------------------------------------------------------------------- /me


async def test_me_requires_cookie(api: Api) -> None:
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_me_returns_masked_phone_balance_and_resolved_options(
    api: Api,
    shop: tuple[SdClient, AccessLink],
    session: AsyncSession,
) -> None:
    client, _ = shop
    from datetime import UTC, datetime

    session.add(SdClientBalance(client_id=client.id, balance=-1500, synced_at=datetime.now(UTC)))
    await session.commit()
    r = await api.get("/api/shop/me")
    assert r.status_code == 200
    body = r.json()
    assert body["client"]["name"] == "Тест Дилшод"
    assert body["client"]["phone"] == "+998 90 *** ** 25" and KEY not in r.text
    assert body["client"]["balance"] == "-1500.00"
    assert body["csrf_token"] == api.csrf
    opts = body["options"]
    assert opts["warehouses"] == {
        "mode": "fixed",
        "options": [{"id": "d0_1", "name": "Основной склад"}],
    }
    assert [o["name"] for o in opts["price_types"]["options"]] == ["НАЛ", "А-ЦЕНА"]
    # client_assigned: only the agents attached to this client in SD
    assert opts["agents"]["mode"] == "client_assigned"
    assert opts["agents"]["options"] == [{"id": "d0_11", "name": "AGENT11"}]
    assert opts["trade_directions"]["options"] == [{"id": "1", "name": "Parfum Brend"}]
    assert "created_by" not in r.text and "login_count" not in r.text


async def test_me_hides_non_sale_price_types_even_if_stored(
    api: Api, seed: Seed, session: AsyncSession
) -> None:
    admin = await seed.admin("root")
    await seed.client("A", [KEY])
    settings = {**DEFAULT_SETTINGS, "price_types": {"mode": "choice", "ids": ["d0_2", "d0_7"]}}
    await seed.link(admin, settings=settings)
    await api.shop_login("shop-link", PHONE)
    opts = (await api.get("/api/shop/me")).json()["options"]
    assert [o["id"] for o in opts["price_types"]["options"]] == ["d0_2"]


# ---------------------------------------------------------------------- logout / audit


async def test_logout_revokes_session_and_audits(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession
) -> None:
    r = await api.post("/api/shop/auth/logout")
    assert r.status_code == 204
    assert 'shop_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]
    row = (await session.scalars(select(ShopSession))).one()
    assert row.revoked_at is not None
    assert (await api.get("/api/shop/me")).status_code == 401
    acts = (await session.scalars(select(AuditLog.action))).all()
    assert "shop.logout" in acts


async def test_revoked_session_rejected_even_with_hot_cache(
    api: Api, shop: tuple[SdClient, AccessLink], redis: FakeAsyncRedis, session: AsyncSession
) -> None:
    assert (await api.get("/api/shop/me")).status_code == 200  # populates cache
    assert await keys(redis, "sess:shop:*")
    client, _ = shop
    n = await sessions.revoke_shop_sessions(session, redis, client_id=client.id)
    assert n == 1
    assert not await keys(redis, "sess:shop:*")
    assert (await api.get("/api/shop/me")).status_code == 401


# ------------------------------------------------------------------------------ CSRF / Origin


async def test_csrf_missing_or_wrong_is_403(api: Api, shop: tuple[SdClient, AccessLink]) -> None:
    r = await api.post("/api/shop/auth/logout", csrf=False)
    assert r.status_code == 403
    r = await api.post("/api/shop/auth/logout", csrf=False, headers={"X-CSRF-Token": "nope"})
    assert r.status_code == 403
    assert (await api.get("/api/shop/me")).status_code == 200  # still logged in
    assert (await api.post("/api/shop/auth/logout")).status_code == 204


async def test_csrf_token_of_another_session_rejected(
    api: Api, shop: tuple[SdClient, AccessLink], app_factory: ApiFactory
) -> None:
    other = Api((await app_factory()).http)
    # same DB/redis: log the same client in again from a second browser
    await other.shop_login("shop-link", PHONE)
    assert other.csrf != api.csrf
    r = await api.post(
        "/api/shop/auth/logout", csrf=False, headers={"X-CSRF-Token": other.csrf or ""}
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": ORIGIN}, 204),
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": "null"}, 403),
        ({"Referer": ORIGIN + "/s/abc"}, 204),
        ({"Referer": "https://evil.example/page"}, 403),
        ({}, 204),
    ],
)
async def test_origin_check_on_state_changing_requests(
    api: Api,
    shop: tuple[SdClient, AccessLink],
    headers: dict[str, str],
    status: int,
) -> None:
    r = await api.post("/api/shop/auth/logout", headers=headers)
    assert r.status_code == status


async def test_origin_checked_on_login_too(api: Api, seed: Seed) -> None:
    r = await api.post(
        "/api/shop/auth/login",
        {"slug": "shop-link", "phone": PHONE},
        csrf=False,
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


# ------------------------------------------------------------------------- expiry / sliding


async def test_session_expiry(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    await session.execute(
        update(ShopSession).values(expires_at=clock.utcnow() - timedelta(seconds=1))
    )
    await session.commit()
    await redis.flushall()
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_cached_session_still_honours_expiry(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    assert (await api.get("/api/shop/me")).status_code == 200
    key = (await keys(redis, "sess:shop:*"))[0]
    import json

    data = json.loads(await redis.get(key))
    data["expires_at"] = (clock.utcnow() - timedelta(seconds=5)).isoformat()
    await redis.set(key, json.dumps(data))
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_sliding_expiry_extends_on_use(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    soon = clock.utcnow() + timedelta(days=1)
    await session.execute(update(ShopSession).values(expires_at=soon))
    await session.commit()
    await redis.flushall()
    assert (await api.get("/api/shop/me")).status_code == 200
    row = (await session.scalars(select(ShopSession))).one()
    await session.refresh(row)
    assert row.expires_at > clock.utcnow() + timedelta(days=29)


async def test_session_cache_ttl_is_60s(
    api: Api, shop: tuple[SdClient, AccessLink], redis: FakeAsyncRedis
) -> None:
    await api.get("/api/shop/me")
    key = (await keys(redis, "sess:shop:*"))[0]
    assert 0 < await redis.ttl(key) <= 60


# ------------------------------------------------------------------------- revalidation


async def test_link_deactivated_ends_sessions(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession
) -> None:
    _, link = shop
    await session.execute(update(AccessLink).where(AccessLink.id == link.id).values(active=False))
    await session.commit()
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_link_expired_ends_sessions(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession
) -> None:
    _, link = shop
    await session.execute(
        update(AccessLink)
        .where(AccessLink.id == link.id)
        .values(
            starts_at=clock.utcnow() - timedelta(days=2), ends_at=clock.utcnow() - timedelta(days=1)
        )
    )
    await session.commit()
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_max_logins_reached_does_not_kill_existing_sessions(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession
) -> None:
    _, link = shop
    await session.execute(update(AccessLink).where(AccessLink.id == link.id).values(max_logins=1))
    await session.commit()
    assert (await api.get("/api/shop/me")).status_code == 200


async def test_client_deactivated_ends_session(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession
) -> None:
    from app.models import SdClient

    client, _ = shop
    await session.execute(update(SdClient).where(SdClient.id == client.id).values(active=False))
    await session.commit()
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_client_block_and_phone_block_noticed_after_revalidation_window(
    api: Api,
    shop: tuple[SdClient, AccessLink],
    session: AsyncSession,
    redis: FakeAsyncRedis,
) -> None:
    client, _ = shop
    assert (await api.get("/api/shop/me")).status_code == 200
    # within the 60s window the cached verdict is used (documented staleness bound)
    session.add(ClientSetting(client_id=client.id, blocked=True))
    await session.commit()
    assert (await api.get("/api/shop/me")).status_code == 200
    await redis.delete(f"shopvalid:{(await session.scalars(select(ShopSession.id))).one()}")
    assert (await api.get("/api/shop/me")).status_code == 401
    assert (await session.scalars(select(ShopSession.revoked_at))).one() is not None


async def test_phone_flag_blocked_noticed_on_revalidation(
    api: Api, shop: tuple[SdClient, AccessLink], session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    assert (await api.get("/api/shop/me")).status_code == 200
    session.add(
        PhoneFlag(phone_key=KEY, status="blocked", client_count=1, updated_at=clock.utcnow())
    )
    await session.commit()
    await redis.flushall()  # simulate the 60s window elapsing
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_revalidation_ttl_configured(
    api: Api, shop: tuple[SdClient, AccessLink], redis: FakeAsyncRedis
) -> None:
    await api.get("/api/shop/me")
    key = (await keys(redis, "shopvalid:*"))[0]
    assert 0 < await redis.ttl(key) <= 60


# ------------------------------------------------------------------------------ rate limits


async def test_ip_rate_limit_minute(app_factory: ApiFactory, seed: Seed) -> None:
    api = await app_factory(make_settings(RL_SHOP_IP_PER_MIN=3))
    codes = []
    for i in range(5):
        r = await api.shop_login("shop-link", f"9055500{i:02d}1"[-9:])
        codes.append(r.status_code)
    assert codes[:3] != [429] * 3 and codes[3:] == [429, 429]
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 429 and 0 < int(r.headers["Retry-After"]) <= 60


async def _with_link(seed: Seed) -> None:
    await seed.link(await seed.admin("root"))


async def test_failed_attempts_hit_phone_limit_minute_and_hour(
    app_factory: ApiFactory, seed: Seed
) -> None:
    await _with_link(seed)
    api = await app_factory(make_settings(RL_SHOP_PHONE_PER_MIN=2, RL_SHOP_PHONE_PER_HOUR=3))
    first = [(await api.shop_login("shop-link", PHONE)).status_code for _ in range(3)]
    assert first[:2] == [401, 401] and first[2] == 429
    assert (await api.shop_login("shop-link", "905551122")).status_code == 401  # other phone
    r = await api.shop_login("shop-link", PHONE)
    assert r.status_code == 429 and 0 < int(r.headers["Retry-After"]) <= 3600


async def test_phone_limit_is_format_independent(app_factory: ApiFactory, seed: Seed) -> None:
    await _with_link(seed)
    api = await app_factory(make_settings(RL_SHOP_PHONE_PER_MIN=2))
    for raw in ("998902996625", "+998 (90) 299-66-25"):
        assert (await api.shop_login("shop-link", raw)).status_code == 401
    assert (await api.shop_login("shop-link", "90 299 66 25")).status_code == 429


async def test_correct_login_bypasses_phone_limit(app_factory: ApiFactory, seed: Seed) -> None:
    await _with_link(seed)
    await seed.client("A", [KEY])
    api = await app_factory(make_settings(RL_SHOP_PHONE_PER_MIN=1, RL_SHOP_SLUG_PER_MIN=1))
    for _ in range(4):
        assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200
    # ...while failures for another phone are throttled
    codes = [(await api.shop_login("shop-link", "905551122")).status_code for _ in range(2)]
    assert codes == [401, 429]
    # and a correct login is still admitted afterwards
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200


async def test_attacker_cannot_lock_out_the_real_owner(app_factory: ApiFactory, seed: Seed) -> None:
    """Failed guesses for OTHER phones exhaust the slug failure budget, not the owner's login."""
    await _with_link(seed)
    await seed.client("A", [KEY])
    api = await app_factory(make_settings(RL_SHOP_SLUG_PER_MIN=3))
    for i in range(5):
        await api.shop_login("shop-link", f"90555{i:04d}")
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200


async def test_slug_failure_limit(app_factory: ApiFactory, seed: Seed) -> None:
    await _with_link(seed)
    api = await app_factory(make_settings(RL_SHOP_SLUG_PER_MIN=2))
    codes = [(await api.shop_login("shop-link", f"90555{i:04d}")).status_code for i in range(3)]
    assert codes == [401, 401, 429]


async def test_link_failure_flood_throttles_failures_only_and_alerts(
    app_factory: ApiFactory, seed: Seed, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    await _with_link(seed)
    await seed.client("A", [KEY])
    api = await app_factory(make_settings(RL_SHOP_LINK_FAIL_THRESHOLD=3))
    caplog.set_level("WARNING")
    logging.getLogger().addHandler(caplog.handler)  # create_app reset the root handlers
    codes = [(await api.shop_login("shop-link", f"90555{i:04d}")).status_code for i in range(6)]
    assert codes == [401, 401, 401, 429, 429, 429]
    r = await api.shop_login("shop-link", "905559999")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert (await Api(api.http).shop_login("shop-link", PHONE)).status_code == 200  # success OK
    alerts = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "shop.link_failure_flood"))
    ).all()
    assert len(alerts) == 1  # once per window
    assert "shop_link_failure_flood" in caplog.text


async def test_link_info_is_rate_limited_per_ip(app_factory: ApiFactory, seed: Seed) -> None:
    await _with_link(seed)
    api = await app_factory(make_settings(RL_SHOP_LINKINFO_IP_PER_MIN=3))
    codes = [(await api.get("/api/shop/links/shop-link")).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    assert "Retry-After" in (await api.get("/api/shop/links/shop-link")).headers


async def test_ipv6_clients_share_a_64_bucket(app_factory: ApiFactory, seed: Seed) -> None:
    await _with_link(seed)
    s = make_settings(RL_SHOP_IP_PER_MIN=2)
    a = await app_factory(s, client_host="2001:db8:1:2::1")
    b = await app_factory(s, client_host="2001:db8:1:2:ffff:ffff:ffff:ffff")  # same /64
    c = await app_factory(s, client_host="2001:db8:1:3::1")  # other /64
    assert (await a.shop_login("shop-link", PHONE)).status_code == 401
    assert (await b.shop_login("shop-link", "905551122")).status_code == 401
    assert (await a.shop_login("shop-link", "905553344")).status_code == 429
    assert (await c.shop_login("shop-link", PHONE)).status_code != 429


async def test_subnet_tier_limits_many_addresses_in_a_24(
    app_factory: ApiFactory, seed: Seed
) -> None:
    await _with_link(seed)
    s = make_settings(RL_SHOP_SUBNET_PER_MIN=3)
    hosts = [await app_factory(s, client_host=f"203.0.113.{i}") for i in range(1, 6)]
    codes = [(await h.shop_login("shop-link", PHONE)).status_code for h in hosts]
    assert codes == [401, 401, 401, 429, 429]
    other = await app_factory(s, client_host="198.51.100.7")
    assert (await other.shop_login("shop-link", PHONE)).status_code == 401


async def test_hourly_ip_limit(app_factory: ApiFactory, seed: Seed) -> None:
    api = await app_factory(make_settings(RL_SHOP_IP_PER_HOUR=2))
    codes = [(await api.shop_login("shop-link", f"90555{i:04d}")).status_code for i in range(3)]
    assert codes[2] == 429


async def test_rate_limit_fails_closed_when_redis_down(app_factory: ApiFactory, seed: Seed) -> None:
    api = await app_factory()

    class Broken:
        def pipeline(self, *a: object, **k: object) -> object:
            from redis.exceptions import ConnectionError as RedisConnError

            raise RedisConnError("down")

    api.http._transport.app.state.redis = Broken()  # type: ignore[attr-defined]
    assert (await api.shop_login("shop-link", PHONE)).status_code == 503


async def test_xff_ignored_unless_trusted_proxy(app_factory: ApiFactory, seed: Seed) -> None:
    api = await app_factory(make_settings(RL_SHOP_IP_PER_MIN=1))
    h = {"X-Forwarded-For": "1.1.1.1"}
    r1 = await api.post(
        "/api/shop/auth/login", {"slug": "shop-link", "phone": PHONE}, csrf=False, headers=h
    )
    h2 = {"X-Forwarded-For": "2.2.2.2"}  # spoofing a new IP must not reset the counter
    r2 = await api.post(
        "/api/shop/auth/login", {"slug": "shop-link", "phone": "905551122"}, csrf=False, headers=h2
    )
    assert r1.status_code != 429 and r2.status_code == 429


async def test_xff_honoured_from_trusted_proxy(app_factory: ApiFactory, seed: Seed) -> None:
    api = await app_factory(
        make_settings(RL_SHOP_IP_PER_MIN=1, TRUSTED_PROXIES="127.0.0.1,10.0.0.0/8")
    )

    async def login(xff: str, phone: str) -> int:
        r = await api.post(
            "/api/shop/auth/login",
            {"slug": "shop-link", "phone": phone},
            csrf=False,
            headers={"X-Forwarded-For": xff},
        )
        return r.status_code

    assert await login("1.1.1.1", PHONE) != 429
    assert await login("2.2.2.2", "905551122") != 429  # different real client
    assert await login("2.2.2.2, 10.1.2.3", "905553344") == 429  # same real client behind 2 proxies

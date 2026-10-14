import logging
from datetime import timedelta

import pyotp
import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock, security
from app.core.security import decrypt_secret
from app.models import AdminSession, AdminUser, AuditLog
from app.services import admin_auth
from tests.api.conftest import ORIGIN, PASSWORD, Api, ApiFactory, make_settings
from tests.api.helpers import Seed, keys

BAD = "Login yoki parol noto'g'ri, yoki akkaunt vaqtincha bloklangan"


@pytest.fixture
async def root(seed: Seed) -> AdminUser:
    return await seed.admin("root")


async def _audit_actions(session: AsyncSession) -> list[str]:
    return list((await session.scalars(select(AuditLog.action).order_by(AuditLog.id))).all())


# ----------------------------------------------------------------------------------- login


async def test_login_success(api: Api, root: AdminUser, session: AsyncSession) -> None:
    r = await api.admin_login("root")
    assert r.status_code == 200
    body = r.json()
    assert body["csrf_token"] and body["must_change_password"] is False
    assert body["user"]["username"] == "root" and "admins.manage" in body["user"]["permissions"]
    assert "password_hash" not in r.text and "totp_secret" not in r.text
    cookie = r.headers["set-cookie"]
    assert "admin_session=" in cookie and "HttpOnly" in cookie and "Path=/api/admin" in cookie
    assert "SameSite=lax" in cookie and "Max-Age=43200" in cookie
    row = (await session.scalars(select(AdminSession))).one()
    token = api.http.cookies.get("admin_session")
    assert token and row.token_hash != token and len(row.token_hash) == 64
    await session.refresh(root)
    assert root.last_login_at is not None
    assert "admin.login" in await _audit_actions(session)


async def test_login_username_case_insensitive(api: Api, root: AdminUser) -> None:
    assert (await api.admin_login("  ROOT ")).status_code == 200


async def test_wrong_password_and_unknown_user_look_identical(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    a = await api.admin_login("root", "wrong-password-1")
    b = await api.admin_login("ghost", "wrong-password-1")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": BAD}
    assert (await _audit_actions(session)).count("admin.login_failed") == 2


async def test_unknown_user_still_runs_a_password_verification(
    api: Api, root: AdminUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    real = security.verify_password

    def spy(password_hash: str, password: str) -> bool:
        seen.append(password_hash)
        return real(password_hash, password)

    monkeypatch.setattr(admin_auth, "verify_password", spy)
    await api.admin_login("ghost", "whatever-pass-1")
    await api.admin_login("root", "whatever-pass-1")
    assert seen[0] == security.DUMMY_HASH and seen[1] != security.DUMMY_HASH
    assert len(seen) == 2


async def _fail_n(api: Api, n: int, user: str = "root") -> None:
    for _ in range(n):
        assert (await api.admin_login(user, "bad-password-1")).status_code == 401


async def test_lockout_is_per_username_and_ip_and_expires(
    app_factory: ApiFactory,
    root: AdminUser,
    session: AsyncSession,
    redis: FakeAsyncRedis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    attacker = await app_factory(client_host="203.0.113.9")
    owner = await app_factory(client_host="198.51.100.4")
    caplog.set_level("WARNING")
    logging.getLogger().addHandler(caplog.handler)  # create_app reset the root handlers
    await _fail_n(attacker, 5)
    # the offending (user, ip) pair is locked: even the right password is refused, generically
    r = await attacker.admin_login("root")
    assert r.status_code == 401 and r.json() == {"detail": BAD}
    # ...but the real owner from another address is NOT locked out (no lockout DoS)
    assert (await owner.admin_login("root")).status_code == 200
    # the lock is scoped to the username too: another account is untouched for the attacker
    await seed_second(session)
    assert (await attacker.admin_login("other")).status_code == 200
    lock = (await keys(redis, "adminlock:*"))[0]
    assert 14 * 60 < await redis.ttl(lock) <= 15 * 60
    # audit + structured warning
    assert "admin.lockout" in await _audit_actions(session)
    assert "admin_lockout" in caplog.text and "203.0.113.9" in caplog.text
    await redis.delete(lock)  # lock expiry
    assert (await attacker.admin_login("root")).status_code == 200


async def seed_second(session: AsyncSession) -> None:
    await Seed(session).admin("other")


async def test_lockout_with_totp_correct_code_from_other_ip_passes(
    app_factory: ApiFactory, root: AdminUser, session: AsyncSession
) -> None:
    a = await app_factory(client_host="203.0.113.9")
    owner = await app_factory(client_host="198.51.100.4")
    secret = await _enable_totp(a, session, root)
    await _fail_n(a, 5)
    code = pyotp.TOTP(secret).at(clock.utcnow() + timedelta(seconds=30))
    assert (await owner.admin_login("root", totp=code)).status_code == 200
    # attacker holding password+TOTP is still locked from the offending address
    code2 = pyotp.TOTP(secret).at(clock.utcnow() - timedelta(seconds=30))
    assert (await a.admin_login("root", totp=code2)).status_code == 401


async def test_per_username_soft_throttle_delays_but_never_locks(
    app_factory: ApiFactory, root: AdminUser, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    s = make_settings(
        ADMIN_MAX_FAILED_LOGINS=100, ADMIN_USER_DELAY_THRESHOLD=2, ADMIN_USER_DELAY_BASE=1.0,
        ADMIN_USER_DELAY_MAX=4.0,
    )  # fmt: skip
    # distributed guessing from many addresses
    clients = [await app_factory(s, client_host=f"198.51.100.{i}") for i in range(1, 8)]
    for c in clients[:6]:
        assert (await c.admin_login("root", "bad-password-1")).status_code == 401
    assert delays == [1.0, 2.0, 4.0, 4.0]  # grows, capped
    assert (await clients[6].admin_login("root")).status_code == 200  # never a hard lock


async def test_owner_not_blocked_by_distributed_failures(
    app_factory: ApiFactory, root: AdminUser
) -> None:
    s = make_settings(ADMIN_MAX_FAILED_LOGINS=2, ADMIN_USER_DELAY_BASE=0)
    for i in range(1, 6):
        c = await app_factory(s, client_host=f"198.51.100.{i}")
        await c.admin_login("root", "bad-password-1")
    owner = await app_factory(s, client_host="192.0.2.50")
    assert (await owner.admin_login("root")).status_code == 200


async def test_success_resets_pair_counter(
    api: Api, root: AdminUser, redis: FakeAsyncRedis
) -> None:
    await _fail_n(api, 3)
    assert (await api.admin_login("root")).status_code == 200
    assert await keys(redis, "adminfail:*:*") == []
    await _fail_n(api, 4)  # would be 7 without the reset
    assert await keys(redis, "adminlock:*") == []


async def test_reset_password_clears_lockout_keys(
    api: Api, root: AdminUser, redis: FakeAsyncRedis
) -> None:
    await _fail_n(api, 5)
    assert await keys(redis, "adminlock:*")
    await admin_auth.clear_lockouts(redis, "ROOT")
    assert not await keys(redis, "adminlock:*") and not await keys(redis, "adminfail:*")


async def test_inactive_user_cannot_login(api: Api, seed: Seed) -> None:
    await seed.admin("gone", active=False)
    assert (await api.admin_login("gone")).status_code == 401


async def test_admin_login_rate_limits(app_factory: ApiFactory, seed: Seed) -> None:
    await seed.admin("root")
    api = await app_factory(make_settings(RL_ADMIN_USER_PER_MIN=3))
    codes = [(await api.admin_login("root", "bad-password-1")).status_code for _ in range(4)]
    assert codes[:3] == [401, 401, 401] and codes[3] == 429
    r = await api.admin_login("root")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


async def test_admin_login_ip_rate_limit(app_factory: ApiFactory) -> None:
    api = await app_factory(make_settings(RL_ADMIN_IP_PER_MIN=2))
    codes = [(await api.admin_login(f"user{i}", "x" * 12)).status_code for i in range(3)]
    assert codes == [401, 401, 429]


# ----------------------------------------------------------------- session / csrf / origin


async def test_me_and_logout(api: Api, root: AdminUser, session: AsyncSession) -> None:
    assert (await api.get("/api/admin/auth/me")).status_code == 401
    await api.admin_login("root")
    me = (await api.get("/api/admin/auth/me")).json()
    assert me["user"]["role"] == "superadmin" and me["csrf_token"] == api.csrf
    assert (await api.post("/api/admin/auth/logout")).status_code == 204
    assert (await api.get("/api/admin/auth/me")).status_code == 401
    assert (await session.scalars(select(AdminSession.revoked_at))).one() is not None
    assert "admin.logout" in await _audit_actions(session)


async def test_csrf_required_on_admin_state_changes(api: Api, root: AdminUser) -> None:
    await api.admin_login("root")
    assert (await api.post("/api/admin/auth/logout", csrf=False)).status_code == 403
    r = await api.post("/api/admin/auth/logout", csrf=False, headers={"X-CSRF-Token": "bad"})
    assert r.status_code == 403
    assert (await api.get("/api/admin/auth/me")).status_code == 200


async def test_admin_origin_check(api: Api, root: AdminUser) -> None:
    await api.admin_login("root")
    r = await api.post("/api/admin/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = await api.post("/api/admin/auth/logout", headers={"Origin": ORIGIN})
    assert r.status_code == 204


async def test_admin_cookie_not_valid_for_shop_api(api: Api, root: AdminUser) -> None:
    await api.admin_login("root")
    assert (await api.get("/api/shop/me")).status_code == 401


async def test_admin_idle_timeout_and_sliding(
    api: Api, root: AdminUser, session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    await api.admin_login("root")
    await session.execute(
        update(AdminSession).values(last_seen_at=clock.utcnow() - timedelta(minutes=61))
    )
    await session.commit()
    await redis.flushall()
    assert (await api.get("/api/admin/auth/me")).status_code == 401

    await api.admin_login("root")
    await session.execute(
        update(AdminSession)
        .where(AdminSession.revoked_at.is_(None))
        .values(last_seen_at=clock.utcnow() - timedelta(minutes=50))
    )
    await session.commit()
    await redis.flushall()
    assert (await api.get("/api/admin/auth/me")).status_code == 200  # within idle window
    row = (
        await session.scalars(select(AdminSession).order_by(AdminSession.id.desc()).limit(1))
    ).one()
    await session.refresh(row)
    assert row.last_seen_at > clock.utcnow() - timedelta(minutes=1)  # slid forward


async def test_admin_absolute_expiry_12h(
    api: Api, root: AdminUser, session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    await api.admin_login("root")
    row = (await session.scalars(select(AdminSession))).one()
    assert timedelta(hours=11, minutes=59) < row.expires_at - clock.utcnow() <= timedelta(hours=12)
    await session.execute(
        update(AdminSession).values(expires_at=clock.utcnow() - timedelta(seconds=1))
    )
    await session.commit()
    await redis.flushall()
    assert (await api.get("/api/admin/auth/me")).status_code == 401


async def test_admin_cache_ttl_and_revocation_invalidates_cache(
    api: Api, root: AdminUser, session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    from app.services import sessions

    await api.admin_login("root")
    await api.get("/api/admin/auth/me")
    key = (await keys(redis, "sess:admin:*"))[0]
    assert 0 < await redis.ttl(key) <= 60
    assert await sessions.revoke_admin_sessions(session, redis, admin_user_id=root.id) == 1
    assert await keys(redis, "sess:admin:*") == []
    assert (await api.get("/api/admin/auth/me")).status_code == 401


async def test_deactivated_admin_with_live_session_is_rejected(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    await api.admin_login("root")
    await session.execute(update(AdminUser).values(active=False))
    await session.commit()
    assert (await api.get("/api/admin/auth/me")).status_code == 401


# ------------------------------------------------------------------------------- password


async def test_change_password(
    app_factory: ApiFactory, root: AdminUser, session: AsyncSession
) -> None:
    a, b = await app_factory(), await app_factory()
    await a.admin_login("root")
    await b.admin_login("root")
    new = "brand-new-passphrase"
    assert (
        await a.post("/api/admin/auth/password", {"current_password": "nope", "new_password": new})
    ).status_code == 400
    assert (
        await a.post(
            "/api/admin/auth/password", {"current_password": PASSWORD, "new_password": "short"}
        )
    ).status_code == 422
    assert (
        await a.post(
            "/api/admin/auth/password", {"current_password": PASSWORD, "new_password": PASSWORD}
        )
    ).status_code == 422
    r = await a.post(
        "/api/admin/auth/password", {"current_password": PASSWORD, "new_password": new}
    )
    assert r.status_code == 204
    assert (await a.get("/api/admin/auth/me")).status_code == 200  # current session survives
    assert (await b.get("/api/admin/auth/me")).status_code == 401  # others revoked
    assert (await Api(a.http).admin_login("root", PASSWORD)).status_code == 401
    assert (await Api(a.http).admin_login("root", new)).status_code == 200
    assert "admin.password_changed" in await _audit_actions(session)
    assert new not in str((await session.scalars(select(AuditLog.after))).all())


async def test_must_change_password_gate(api: Api, seed: Seed) -> None:
    await seed.admin("temp", must_change_password=True)
    r = await api.admin_login("temp")
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert (await api.get("/api/admin/auth/me")).status_code == 200
    for url in ("/api/admin/links", "/api/admin/users", "/api/admin/sync/status"):
        r = await api.get(url)
        assert r.status_code == 403 and r.json()["detail"] == "password_change_required"
    new = "another-long-passphrase"
    assert (
        await api.post(
            "/api/admin/auth/password", {"current_password": PASSWORD, "new_password": new}
        )
    ).status_code == 204


async def test_must_change_password_cleared_after_change(api: Api, seed: Seed) -> None:
    await seed.admin("temp", must_change_password=True)
    await api.admin_login("temp")
    new = "another-long-passphrase"
    await api.post("/api/admin/auth/password", {"current_password": PASSWORD, "new_password": new})
    assert (await api.get("/api/admin/links")).status_code == 200
    assert (await api.get("/api/admin/auth/me")).json()["user"]["must_change_password"] is False


# ----------------------------------------------------------------------------------- TOTP


async def _enable_totp(api: Api, session: AsyncSession, user: AdminUser) -> str:
    await api.admin_login(user.username)
    r = await api.post("/api/admin/auth/totp/setup", {"password": PASSWORD})
    assert r.status_code == 200
    secret: str = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/Auto-Shop:")
    assert secret in r.json()["otpauth_uri"]
    assert (
        await api.post(
            "/api/admin/auth/totp/enable", {"code": pyotp.TOTP(secret).now(), "password": PASSWORD}
        )
    ).status_code == 204
    await api.post("/api/admin/auth/logout")
    return secret


async def test_totp_setup_enable_login_flow(
    api: Api,
    root: AdminUser,
    session: AsyncSession,
    app_factory: ApiFactory,
) -> None:
    secret = await _enable_totp(api, session, root)
    await session.refresh(root)
    assert root.totp_enabled and root.totp_secret and root.totp_secret != secret
    assert decrypt_secret(make_settings(), root.totp_secret) == secret  # stored encrypted
    assert secret not in str((await session.scalars(select(AuditLog.after))).all())

    r = await api.admin_login("root")  # no code
    assert r.status_code == 401 and r.json()["detail"] == "totp_required"
    r = await api.admin_login("root", totp="000000")
    assert r.status_code == 401 and r.json()["detail"] == BAD
    code = pyotp.TOTP(secret).at(clock.utcnow() + timedelta(seconds=30))  # valid_window=1
    assert (await api.admin_login("root", totp=code)).status_code == 200


async def test_totp_code_reuse_rejected(api: Api, root: AdminUser, session: AsyncSession) -> None:
    secret = await _enable_totp(api, session, root)
    code = pyotp.TOTP(secret).at(clock.utcnow() - timedelta(seconds=30))
    other = Api(api.http)
    assert (await other.admin_login("root", totp=code)).status_code == 200
    await other.post("/api/admin/auth/logout")
    assert (await Api(api.http).admin_login("root", totp=code)).status_code == 401  # replay


async def test_enable_code_cannot_be_replayed_at_login(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    await api.admin_login("root")
    secret = (await api.post("/api/admin/auth/totp/setup", {"password": PASSWORD})).json()["secret"]
    code = pyotp.TOTP(secret).now()
    assert (
        await api.post("/api/admin/auth/totp/enable", {"code": code, "password": PASSWORD})
    ).status_code == 204
    await api.post("/api/admin/auth/logout")
    assert (await Api(api.http).admin_login("root", totp=code)).status_code == 401


async def test_wrong_totp_counts_towards_lockout(
    api: Api, root: AdminUser, session: AsyncSession, redis: FakeAsyncRedis
) -> None:
    await _enable_totp(api, session, root)
    for _ in range(5):
        assert (await api.admin_login("root", totp="123456")).status_code == 401
    assert await keys(redis, "adminlock:*")


async def test_totp_enable_requires_setup_and_valid_code(api: Api, root: AdminUser) -> None:
    await api.admin_login("root")
    assert (
        await api.post("/api/admin/auth/totp/enable", {"code": "123456", "password": PASSWORD})
    ).status_code == 409
    await api.post("/api/admin/auth/totp/setup", {"password": PASSWORD})
    assert (
        await api.post("/api/admin/auth/totp/enable", {"code": "000000", "password": PASSWORD})
    ).status_code == 400
    assert (
        await api.post("/api/admin/auth/totp/enable", {"code": "abcdef", "password": PASSWORD})
    ).status_code == 400


async def test_totp_disable_needs_password_and_code(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    secret = await _enable_totp(api, session, root)
    code = pyotp.TOTP(secret).at(clock.utcnow() + timedelta(seconds=30))
    await api.admin_login("root", totp=code)
    next_code = pyotp.TOTP(secret).at(clock.utcnow() - timedelta(seconds=30))
    r = await api.post(
        "/api/admin/auth/totp/disable", {"password": "wrong-password", "code": next_code}
    )
    assert r.status_code == 400
    r = await api.post("/api/admin/auth/totp/disable", {"password": PASSWORD, "code": "000000"})
    assert r.status_code == 400
    r = await api.post("/api/admin/auth/totp/disable", {"password": PASSWORD, "code": next_code})
    assert r.status_code == 204
    await session.refresh(root)
    assert root.totp_enabled is False and root.totp_secret is None
    assert (
        await api.post("/api/admin/auth/totp/disable", {"password": PASSWORD, "code": next_code})
    ).status_code == 409
    actions = await _audit_actions(session)
    assert {"admin.totp_setup", "admin.totp_enabled", "admin.totp_disabled"} <= set(actions)


async def test_totp_setup_blocked_when_already_enabled(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    secret = await _enable_totp(api, session, root)
    code = pyotp.TOTP(secret).at(clock.utcnow() + timedelta(seconds=30))
    await api.admin_login("root", totp=code)
    assert (await api.post("/api/admin/auth/totp/setup", {"password": PASSWORD})).status_code == 409


# ------------------------------------------------------------------------------------ RBAC


READ_ENDPOINTS = {
    "links": "/api/admin/links",
    "clients": "/api/admin/clients",
    "phones": "/api/admin/phones",
    "sync": "/api/admin/sync/status",
    "users": "/api/admin/users",
}

# role -> endpoints that must answer 200 (all others must be 403)
EXPECTED = {
    "superadmin": {"links", "clients", "phones", "sync", "users"},
    "admin": {"links", "clients", "phones", "sync"},
    "operator": {"links", "clients", "phones"},
    "viewer": {"links", "clients", "phones", "sync"},
}


@pytest.fixture
def fake_sync(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.admin import sync as sync_api

    async def status(_ctx: object) -> dict[str, object]:
        return {"runs": []}

    monkeypatch.setattr(sync_api, "latest_runs", status)
    api.http._transport.app.state.sync_ctx = object()  # type: ignore[attr-defined]


@pytest.mark.parametrize("role", list(EXPECTED))
async def test_rbac_read_matrix(api: Api, seed: Seed, fake_sync: None, role: str) -> None:
    await seed.admin(f"u-{role}", role=role)
    await api.admin_login(f"u-{role}")
    for name, url in READ_ENDPOINTS.items():
        r = await api.get(url)
        assert r.status_code == (200 if name in EXPECTED[role] else 403), (role, name, r.text)


async def test_rbac_write_matrix(api: Api, seed: Seed, fake_sync: None) -> None:
    await seed.admin("viewer1", role="viewer")
    await api.admin_login("viewer1")
    assert (await api.post("/api/admin/links", {})).status_code == 403  # RBAC precedes validation
    assert (await api.post("/api/admin/clients/1/block", {})).status_code == 403
    assert (await api.post("/api/admin/sync/clients")).status_code == 403
    assert (await api.put("/api/admin/phones/902996625", {"status": "allowed"})).status_code == 403
    assert (await api.post("/api/admin/users", {})).status_code == 403


async def test_extra_permissions_extend_role(api: Api, seed: Seed) -> None:
    await seed.admin("v", role="viewer", extra=["links.write"])
    await api.admin_login("v")
    r = await api.post("/api/admin/links", {})
    assert r.status_code == 422  # passed RBAC, failed body validation
    assert (await api.get("/api/admin/users")).status_code == 403
    me = (await api.get("/api/admin/auth/me")).json()["user"]
    assert "links.write" in me["permissions"] and me["extra_permissions"] == ["links.write"]


async def test_unauthenticated_requests_are_401(api: Api) -> None:
    for url in READ_ENDPOINTS.values():
        assert (await api.get(url)).status_code == 401


async def test_role_change_takes_effect_immediately(
    api: Api, seed: Seed, session: AsyncSession, fake_sync: None
) -> None:
    u = await seed.admin("op", role="operator")
    await api.admin_login("op")
    assert (await api.get("/api/admin/sync/status")).status_code == 403
    await session.execute(update(AdminUser).where(AdminUser.id == u.id).values(role="viewer"))
    await session.commit()
    assert (await api.get("/api/admin/sync/status")).status_code == 200


# ------------------------------------------------ password-verifying endpoints (finding 11)


async def test_totp_setup_and_enable_require_current_password(api: Api, root: AdminUser) -> None:
    await api.admin_login("root")
    bad = {"password": "not-my-password"}
    assert (await api.post("/api/admin/auth/totp/setup", bad)).status_code == 400
    assert (await api.post("/api/admin/auth/totp/setup", {})).status_code == 422
    secret = (await api.post("/api/admin/auth/totp/setup", {"password": PASSWORD})).json()["secret"]
    code = pyotp.TOTP(secret).now()
    r = await api.post("/api/admin/auth/totp/enable", {"code": code, "password": "not-my-password"})
    assert r.status_code == 400
    # a wrong password must not burn the one-time code
    r = await api.post("/api/admin/auth/totp/enable", {"code": code, "password": PASSWORD})
    assert r.status_code == 204


async def test_session_hijacker_cannot_enrol_their_own_totp(api: Api, root: AdminUser) -> None:
    await api.admin_login("root")
    assert (await api.post("/api/admin/auth/totp/setup", {"password": "guess"})).status_code == 400
    assert (await api.get("/api/admin/auth/me")).json()["user"]["totp_enabled"] is False


async def test_password_endpoints_rate_limited_per_session(
    app_factory: ApiFactory, root: AdminUser
) -> None:
    api = await app_factory(make_settings(RL_ADMIN_PASSWORD_PER_MIN=3))
    await api.admin_login("root")
    codes = [
        (
            await api.post(
                "/api/admin/auth/password",
                {"current_password": f"wrong-guess-{i}", "new_password": "brand-new-passphrase"},
            )
        ).status_code
        for i in range(5)
    ]
    assert codes == [400, 400, 400, 429, 429]
    r = await api.post("/api/admin/auth/totp/setup", {"password": "x"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


async def test_password_limit_is_per_user_across_sessions(
    app_factory: ApiFactory, root: AdminUser
) -> None:
    s = make_settings(RL_ADMIN_PASSWORD_PER_MIN=2)  # per-user budget is 2x = 4 per minute
    sessions_ = []
    for _ in range(3):
        a = await app_factory(s)
        await a.admin_login("root")
        sessions_.append(a)
    codes = []
    for a in sessions_:
        for _ in range(2):
            r = await a.post(
                "/api/admin/auth/password",
                {"current_password": "wrong-guess-x", "new_password": "brand-new-passphrase"},
            )
            codes.append(r.status_code)
    assert codes[:4] == [400] * 4 and 429 in codes[4:]

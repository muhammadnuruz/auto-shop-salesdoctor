import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminSession, AdminUser, AuditLog
from tests.api.conftest import PASSWORD, Api, ApiFactory
from tests.api.helpers import Seed

URL = "/api/admin/users"


@pytest.fixture
async def root(api: Api, seed: Seed) -> AdminUser:
    user = await seed.admin("root")
    await api.admin_login("root")
    return user


async def test_create_user_with_generated_temp_password(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    r = await api.post(URL, {"username": "Oper1", "full_name": "Op One", "role": "operator"})
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "oper1" and body["must_change_password"] is True
    temp = body["temporary_password"]
    assert len(temp) >= 15 and "password_hash" not in r.text
    # listing / get never expose it again
    assert "temporary_password" not in (await api.get(f"{URL}/{body['id']}")).text
    assert "temporary_password" not in (await api.get(URL)).text
    other = Api(api.http)
    login = await other.admin_login("oper1", temp)
    assert login.status_code == 200 and login.json()["must_change_password"] is True
    audit = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "admin_user.create"))
    ).one()
    assert temp not in str(audit.after) and audit.actor_id == root.id


async def test_create_user_with_given_password_and_validation(api: Api, root: AdminUser) -> None:
    r = await api.post(
        URL, {"username": "viewer1", "role": "viewer", "password": "a-decent-passphrase"}
    )
    assert r.status_code == 201 and "temporary_password" not in r.json()
    assert (
        await api.post(URL, {"username": "weak", "role": "viewer", "password": "short"})
    ).status_code == 422
    assert (await api.post(URL, {"username": "x y", "role": "viewer"})).status_code == 422
    assert (await api.post(URL, {"username": "okname", "role": "boss"})).status_code == 422
    r = await api.post(
        URL, {"username": "okname", "role": "viewer", "extra_permissions": ["nope.nope"]}
    )
    assert r.status_code == 422


async def test_duplicate_username_409(api: Api, root: AdminUser) -> None:
    assert (await api.post(URL, {"username": "dup", "role": "viewer"})).status_code == 201
    assert (await api.post(URL, {"username": "DUP", "role": "viewer"})).status_code == 409


async def test_update_role_permissions_and_active(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    uid = (await api.post(URL, {"username": "worker", "role": "viewer"})).json()["id"]
    r = await api.patch(
        f"{URL}/{uid}", {"role": "operator", "extra_permissions": ["links.write"], "full_name": "W"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "operator" and "links.write" in body["permissions"]
    assert (await api.patch(f"{URL}/{uid}", {"active": False})).json()["active"] is False
    a = (
        await session.scalars(select(AuditLog).where(AuditLog.action == "admin_user.update"))
    ).all()
    assert len(a) == 2 and a[0].before["role"] == "viewer" and a[0].after["role"] == "operator"  # type: ignore[index]
    assert (await api.patch(f"{URL}/99999", {"active": False})).status_code == 404


async def test_deactivation_revokes_sessions(
    app_factory: ApiFactory, api: Api, root: AdminUser, seed: Seed, session: AsyncSession
) -> None:
    u = await seed.admin("victim", role="admin")
    victim = await app_factory()
    await victim.admin_login("victim")
    assert (await victim.get("/api/admin/auth/me")).status_code == 200
    await api.patch(f"{URL}/{u.id}", {"active": False})
    assert (await victim.get("/api/admin/auth/me")).status_code == 401


async def test_last_superadmin_cannot_be_demoted_deactivated_or_deleted(
    api: Api, root: AdminUser
) -> None:
    assert (await api.patch(f"{URL}/{root.id}", {"role": "admin"})).status_code == 409
    assert (await api.patch(f"{URL}/{root.id}", {"active": False})).status_code == 409
    assert (await api.delete(f"{URL}/{root.id}")).status_code == 409  # self -> 409 either way
    assert (await api.get("/api/admin/auth/me")).status_code == 200


async def test_inactive_superadmin_does_not_count_as_another(
    api: Api, root: AdminUser, seed: Seed
) -> None:
    await seed.admin("dormant", role="superadmin", active=False)
    assert (await api.patch(f"{URL}/{root.id}", {"role": "admin"})).status_code == 409


async def test_second_superadmin_allows_demoting_first(
    app_factory: ApiFactory, api: Api, root: AdminUser, seed: Seed
) -> None:
    other = await seed.admin("second", role="superadmin")
    assert (await api.patch(f"{URL}/{other.id}", {"role": "admin"})).status_code == 200
    # now `root` is the last one again
    assert (await api.patch(f"{URL}/{root.id}", {"role": "admin"})).status_code == 409


async def test_delete_other_superadmin_only_if_not_last(
    api: Api, root: AdminUser, seed: Seed
) -> None:
    other = await seed.admin("second", role="superadmin")
    assert (await api.delete(f"{URL}/{other.id}")).status_code == 204
    gone = await seed.admin("third", role="admin")
    assert (await api.delete(f"{URL}/{gone.id}")).status_code == 204
    assert (await api.get(f"{URL}/{gone.id}")).status_code == 404


async def test_cannot_delete_or_deactivate_self(api: Api, root: AdminUser, seed: Seed) -> None:
    await seed.admin("second", role="superadmin")
    assert (await api.delete(f"{URL}/{root.id}")).status_code == 409
    assert (await api.patch(f"{URL}/{root.id}", {"active": False})).status_code == 409


async def test_delete_user_with_links_conflicts(api: Api, root: AdminUser, seed: Seed) -> None:
    owner = await seed.admin("owner", role="admin")
    await seed.link(owner)
    assert (await api.delete(f"{URL}/{owner.id}")).status_code == 409
    assert (await api.get(f"{URL}/{owner.id}")).status_code == 200


async def test_reset_password_returns_temp_once_and_forces_change(
    app_factory: ApiFactory,
    api: Api,
    root: AdminUser,
    seed: Seed,
    session: AsyncSession,
) -> None:
    u = await seed.admin("worker", role="operator")
    live = await app_factory()
    await live.admin_login("worker")
    r = await api.post(f"{URL}/{u.id}/reset-password")
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert (await live.get("/api/admin/auth/me")).status_code == 401  # sessions revoked
    assert (await Api(api.http).admin_login("worker", PASSWORD)).status_code == 401
    login = await Api(api.http).admin_login("worker", temp)
    assert login.status_code == 200 and login.json()["must_change_password"] is True
    audit = (
        await session.scalars(
            select(AuditLog).where(AuditLog.action == "admin_user.reset_password")
        )
    ).one()
    assert temp not in str(audit.after) and temp not in str(audit.before)


async def test_reset_password_clears_lockout(api: Api, root: AdminUser, seed: Seed) -> None:
    u = await seed.admin("locked", role="viewer")
    other = Api(api.http)
    for _ in range(5):
        await other.admin_login("locked", "bad-password-1")
    temp = (await api.post(f"{URL}/{u.id}/reset-password")).json()["temporary_password"]
    assert (await Api(api.http).admin_login("locked", temp)).status_code == 200


async def test_revoke_sessions(
    app_factory: ApiFactory, api: Api, root: AdminUser, seed: Seed, session: AsyncSession
) -> None:
    u = await seed.admin("worker", role="operator")
    a, b = await app_factory(), await app_factory()
    await a.admin_login("worker")
    await b.admin_login("worker")
    r = await api.post(f"{URL}/{u.id}/revoke-sessions")
    assert r.status_code == 200 and r.json() == {"revoked": 2}
    assert (await a.get("/api/admin/auth/me")).status_code == 401
    assert (await b.get("/api/admin/auth/me")).status_code == 401
    rows = (
        await session.scalars(select(AdminSession).where(AdminSession.admin_user_id == u.id))
    ).all()
    assert all(x.revoked_at for x in rows)


# -------------------------------------------------------------- privilege escalation guards


async def test_non_superadmin_cannot_manage_superadmins(
    app_factory: ApiFactory, api: Api, root: AdminUser, seed: Seed
) -> None:
    await seed.admin("mgr", role="admin", extra=["admins.manage"])
    mgr = await app_factory()
    await mgr.admin_login("mgr")
    assert (await mgr.post(URL, {"username": "evil", "role": "superadmin"})).status_code == 403
    assert (await mgr.patch(f"{URL}/{root.id}", {"active": False})).status_code == 403
    assert (await mgr.delete(f"{URL}/{root.id}")).status_code == 403
    assert (await mgr.post(f"{URL}/{root.id}/reset-password")).status_code == 403
    assert (await mgr.post(f"{URL}/{root.id}/revoke-sessions")).status_code == 403


async def test_cannot_grant_permissions_you_do_not_hold(
    app_factory: ApiFactory, api: Api, root: AdminUser, seed: Seed
) -> None:
    # 'viewer' + admins.manage can create viewers, but not an 'admin' or extra perms it lacks
    await seed.admin("mgr", role="viewer", extra=["admins.manage"])
    mgr = await app_factory()
    await mgr.admin_login("mgr")
    assert (await mgr.post(URL, {"username": "ok1", "role": "viewer"})).status_code == 201
    assert (await mgr.post(URL, {"username": "bad1", "role": "admin"})).status_code == 403
    r = await mgr.post(
        URL, {"username": "bad2", "role": "viewer", "extra_permissions": ["orders.cancel"]}
    )
    assert r.status_code == 403
    r = await mgr.patch(
        f"{URL}/{(await mgr.get('/api/admin/auth/me')).json()['user']['id']}", {"role": "admin"}
    )
    assert r.status_code == 403  # no self-promotion


async def test_audit_rows_written_for_admin_changes_without_secrets(
    api: Api, root: AdminUser, session: AsyncSession
) -> None:
    created = (
        await api.post(
            URL, {"username": "audited", "role": "viewer", "password": "super-secret-pass"}
        )
    ).json()
    await api.patch(f"{URL}/{created['id']}", {"role": "operator"})
    await api.post(f"{URL}/{created['id']}/reset-password")
    await api.delete(f"{URL}/{created['id']}")
    rows = (
        await session.scalars(
            select(AuditLog).where(
                AuditLog.entity_type == "admin_user", AuditLog.entity_id == str(created["id"])
            )
        )
    ).all()
    assert [r.action for r in rows] == [
        "admin_user.create",
        "admin_user.update",
        "admin_user.reset_password",
        "admin_user.delete",
    ]
    blob = str([(r.before, r.after) for r in rows])
    assert "super-secret-pass" not in blob and "argon2" not in blob and "hash" not in blob
    assert all(r.actor_type == "admin" and r.actor_id == root.id and r.request_id for r in rows)


# ---------------------------------------- delegated admins.manage must not touch stronger users


@pytest.fixture
async def delegate(app_factory: ApiFactory, root: AdminUser, seed: Seed) -> Api:
    """A viewer who was given admins.manage (the weakest possible delegate)."""
    await seed.admin("mgr", role="viewer", extra=["admins.manage"])
    mgr = await app_factory()
    await mgr.admin_login("mgr")
    return mgr


@pytest.mark.parametrize(
    "target_role", ["superadmin", "admin", "operator"]
)  # all stronger than a viewer
async def test_delegate_cannot_touch_stronger_users(
    delegate: Api, seed: Seed, target_role: str
) -> None:
    t = await seed.admin(f"t-{target_role}", role=target_role)
    u = f"{URL}/{t.id}"
    assert (await delegate.patch(u, {"full_name": "pwned"})).status_code == 403
    assert (await delegate.patch(u, {"active": False})).status_code == 403
    assert (await delegate.post(f"{u}/reset-password")).status_code == 403
    assert (await delegate.post(f"{u}/revoke-sessions")).status_code == 403
    assert (await delegate.delete(u)).status_code == 403


async def test_delegate_cannot_touch_user_with_extra_permissions_it_lacks(
    delegate: Api, seed: Seed
) -> None:
    # same role rank (viewer) but holds links.write, which the delegate does not
    t = await seed.admin("strong-viewer", role="viewer", extra=["links.write"])
    assert (await delegate.post(f"{URL}/{t.id}/reset-password")).status_code == 403
    assert (await delegate.patch(f"{URL}/{t.id}", {"active": False})).status_code == 403


async def test_delegate_can_manage_equal_or_weaker_users(delegate: Api, seed: Seed) -> None:
    weak = await seed.admin("weak", role="viewer")
    u = f"{URL}/{weak.id}"
    assert (await delegate.patch(u, {"full_name": "ok"})).status_code == 200
    r = await delegate.post(f"{u}/reset-password")
    assert r.status_code == 200 and r.json()["temporary_password"]
    assert (await delegate.post(f"{u}/revoke-sessions")).status_code == 200
    assert (await delegate.delete(u)).status_code == 204


async def test_admin_with_manage_cannot_reset_superadmin_password(
    app_factory: ApiFactory, root: AdminUser, seed: Seed
) -> None:
    await seed.admin("adm", role="admin", extra=["admins.manage"])
    adm = await app_factory()
    await adm.admin_login("adm")
    assert (await adm.post(f"{URL}/{root.id}/reset-password")).status_code == 403
    peer = await seed.admin("operator1", role="operator")
    assert (await adm.post(f"{URL}/{peer.id}/reset-password")).status_code == 200


async def test_superadmin_can_still_manage_everyone(api: Api, root: AdminUser, seed: Seed) -> None:
    t = await seed.admin("t", role="admin")
    assert (await api.post(f"{URL}/{t.id}/reset-password")).status_code == 200

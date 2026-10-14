from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.models import AdminSession, AuditLog, ShopSession
from app.services.retention import purge_auth_data
from app.workers.settings import WorkerSettings, purge_auth_job
from tests.api.helpers import Seed


async def test_purge_removes_only_old_expired_or_revoked(session: AsyncSession) -> None:
    seed = Seed(session)
    await seed.reference_data()
    admin = await seed.admin("root")
    client = await seed.client("A", ["902996625"])
    link = await seed.link(admin)
    now = clock.utcnow()
    day = timedelta(days=1)

    def shop(token: str, **kw: object) -> ShopSession:
        return ShopSession(
            token_hash=token, client_id=client.id, link_id=link.id, phone_key="902996625",
            expires_at=kw.pop("expires_at", now + 30 * day), **kw,
        )  # fmt: skip

    session.add_all(
        [
            shop("live"),
            shop("expired-old", expires_at=now - 8 * day),
            shop("expired-recent", expires_at=now - 2 * day),
            shop("revoked-old", revoked_at=now - 8 * day),
            shop("revoked-recent", revoked_at=now - 1 * day),
            AdminSession(token_hash="a-live", admin_user_id=admin.id, expires_at=now + day),
            AdminSession(token_hash="a-old", admin_user_id=admin.id, expires_at=now - 9 * day),
            AdminSession(
                token_hash="a-revoked", admin_user_id=admin.id, expires_at=now + day,
                revoked_at=now - 9 * day,
            ),
        ]
    )  # fmt: skip
    session.add_all(
        [
            AuditLog(actor_type="system", action="old", at=now - 400 * day),
            AuditLog(actor_type="system", action="recent", at=now - 10 * day),
        ]
    )
    await session.commit()
    out = await purge_auth_data(session, Settings(ENV="test"))
    assert out == {"shop_sessions": 2, "admin_sessions": 2, "audit_rows": 1}
    left = set((await session.scalars(select(ShopSession.token_hash))).all())
    assert left == {"live", "expired-recent", "revoked-recent"}
    assert set((await session.scalars(select(AdminSession.token_hash))).all()) == {"a-live"}
    actions = set((await session.scalars(select(AuditLog.action))).all())
    assert "old" not in actions and "recent" in actions


async def test_retention_is_configurable(session: AsyncSession) -> None:
    now = clock.utcnow()
    session.add(AuditLog(actor_type="system", action="x", at=now - 40 * timedelta(days=1)))
    await session.commit()
    out = await purge_auth_data(session, Settings(ENV="test", AUDIT_RETENTION_DAYS=30))
    assert out["audit_rows"] == 1
    await session.execute(update(AuditLog).values(action="y"))


def test_daily_job_is_registered_in_worker() -> None:
    assert purge_auth_job in WorkerSettings.functions
    assert any(j.coroutine is purge_auth_job for j in WorkerSettings.cron_jobs)

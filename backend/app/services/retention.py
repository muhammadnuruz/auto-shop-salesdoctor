"""Retention: purge expired/revoked sessions and old audit rows (daily arq job)."""

from datetime import timedelta

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.models import AdminSession, AuditLog, ShopSession


async def purge_auth_data(db: AsyncSession, settings: Settings) -> dict[str, int]:
    now = clock.utcnow()
    cutoff = now - timedelta(days=settings.SESSION_PURGE_AFTER_DAYS)
    out: dict[str, int] = {}
    for name, model in (("shop_sessions", ShopSession), ("admin_sessions", AdminSession)):
        res = await db.execute(
            delete(model).where(or_(model.expires_at < cutoff, model.revoked_at < cutoff))
        )
        out[name] = res.rowcount  # type: ignore[attr-defined]
    res = await db.execute(
        delete(AuditLog).where(AuditLog.at < now - timedelta(days=settings.AUDIT_RETENTION_DAYS))
    )
    out["audit_rows"] = res.rowcount  # type: ignore[attr-defined]
    await db.commit()
    return out

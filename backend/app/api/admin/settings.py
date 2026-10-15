"""Runtime settings: sync intervals, phone-flag threshold, Telegram (settings.write)."""

from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import (
    AdminContext,
    DbDep,
    MetaDep,
    RedisDep,
    SettingsDep,
    enforce_rate_limit,
    require,
)
from app.core.config import Settings
from app.core.ratelimit import Limit
from app.services import app_settings, audit, notifier
from app.services.app_settings import SettingsUpdate

router = APIRouter(prefix="/settings", tags=["admin-settings"])
WriteDep = Annotated[AdminContext, Depends(require("settings.write"))]


def _sd_info(settings: Settings) -> dict[str, Any]:
    """Connection facts that are safe to show (host only; never login/password)."""
    return {
        "host": urlsplit(settings.SD_BASE_URL).hostname,
        "credentials_configured": bool(
            settings.SD_LOGIN and settings.SD_PASSWORD.get_secret_value()
        ),
        "filial_id": settings.SD_FILIAL_ID,
    }


async def _view(db: DbDep, settings: Settings) -> dict[str, Any]:
    rt = await app_settings.load(db, settings)
    view = app_settings.public_view(rt, settings)
    view["telegram"]["health"] = await notifier.health(db)
    return {**view, "sd": _sd_info(settings)}


@router.get("")
async def get_settings(ctx: WriteDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    return await _view(db, settings)


@router.put("")
async def update_settings(
    body: SettingsUpdate, ctx: WriteDep, db: DbDep, settings: SettingsDep, meta: MetaDep
) -> dict[str, Any]:
    await app_settings.lock(db)  # read "before" under the same lock as the write
    before = app_settings.public_view(await app_settings.load(db, settings), settings)
    await app_settings.apply_update(db, settings, body, ctx.user.id)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="settings.update",
        entity_type="settings",
        entity_id="app",
        before={k: before[k] for k in ("sync_intervals", "phone_max_clients", "telegram")},
        after=app_settings.audit_view(body),
        meta=meta,
    )
    await db.commit()
    return await _view(db, settings)


@router.post("/telegram/test")
async def telegram_test(
    request: Request,
    ctx: WriteDep,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> dict[str, Any]:
    await enforce_rate_limit(redis, [Limit(f"tgtest:{ctx.user.id}", 5, 60)])
    rt = await app_settings.load(db, settings)
    cfg = rt.telegram
    if not cfg.token or not cfg.chat_ids:
        raise HTTPException(409, "Telegram bot token and at least one chat id are required")
    http: httpx.AsyncClient = request.app.state.http
    results = await notifier.send_test(http, cfg)
    audit.record(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="settings.telegram_test",
        entity_type="settings",
        entity_id="telegram",
        after={"results": results},
        meta=meta,
    )
    await db.commit()
    return {"results": results, "ok": all(v == "ok" for v in results.values())}

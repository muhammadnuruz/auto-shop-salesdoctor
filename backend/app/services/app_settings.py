"""Runtime settings stored in ``app_setting`` (admin-editable), falling back to env defaults.

Only whitelisted keys exist. Secrets (the Telegram bot token) are stored Fernet-encrypted and
never returned by any API: readers only learn whether a token is configured.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models import AppSetting

SYNC_ENTITIES = ("references", "products", "prices", "stock", "clients", "balances")
# admin-settable bounds per entity (seconds); env defaults may be outside (e.g. dev values)
INTERVAL_BOUNDS: dict[str, tuple[int, int]] = {
    "stock": (30, 3600),
    "balances": (60, 86400),
    "products": (60, 86400),
    "prices": (60, 86400),
    "clients": (300, 86400),
    "references": (300, 86400),
}
NOTIFY_EVENTS = (
    "new_order",
    "order_failed",
    "outcome_unknown",
    "reconcile_exhausted",
    "sync_error",
)
NotifyEvent = Literal[
    "new_order", "order_failed", "outcome_unknown", "reconcile_exhausted", "sync_error"
]

KEY_SYNC_INTERVALS = "sync.intervals"
KEY_PHONE_MAX_CLIENTS = "phone.max_clients"
KEY_TELEGRAM = "telegram"
KEY_TELEGRAM_CURSOR = "telegram.cursor"  # internal (notifier position); never exposed
SETTINGS_LOCK_KEY = 0x5E771265  # pg advisory lock id for settings updates

CHAT_ID_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")
BOT_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")


def env_intervals(settings: Settings) -> dict[str, int]:
    return {
        "stock": settings.SYNC_STOCK_INTERVAL,
        "balances": settings.SYNC_BALANCES_INTERVAL,
        "products": settings.SYNC_PRODUCTS_INTERVAL,
        "prices": settings.SYNC_PRICES_INTERVAL,
        "clients": settings.SYNC_CLIENTS_INTERVAL,
        "references": settings.SYNC_REFERENCES_INTERVAL,
    }


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    token: str | None = None  # decrypted; only for the notifier
    chat_ids: tuple[str, ...] = ()
    events: frozenset[str] = frozenset(NOTIFY_EVENTS)

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.token) and bool(self.chat_ids)


@dataclass(frozen=True)
class RuntimeSettings:
    sync_intervals: dict[str, int]
    sync_overrides: dict[str, int]
    phone_max_clients: int
    phone_max_clients_override: int | None
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    telegram_token_error: bool = False  # stored token could not be decrypted (key rotated)


async def _raw(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(select(AppSetting.key, AppSetting.value))).all()
    return {row[0]: row[1] for row in rows}


def _int_or_none(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


async def load(db: AsyncSession, settings: Settings) -> RuntimeSettings:
    raw = await _raw(db)
    overrides: dict[str, int] = {}
    stored = raw.get(KEY_SYNC_INTERVALS)
    if isinstance(stored, dict):
        for entity, value in stored.items():
            iv = _int_or_none(value)
            if entity in INTERVAL_BOUNDS and iv is not None:
                lo, hi = INTERVAL_BOUNDS[entity]
                overrides[entity] = min(max(iv, lo), hi)
    intervals = {**env_intervals(settings), **overrides}

    phone_override = _int_or_none(raw.get(KEY_PHONE_MAX_CLIENTS))
    if phone_override is not None and not 1 <= phone_override <= 50:
        phone_override = None

    tg = raw.get(KEY_TELEGRAM)
    telegram = TelegramConfig()
    token_error = False
    if isinstance(tg, dict):
        token: str | None = None
        enc = tg.get("bot_token_enc")
        if isinstance(enc, str) and enc:
            try:
                token = decrypt_secret(settings, enc)
            except Exception:  # InvalidToken after a key change: treat as not configured
                token_error = True
        chat_ids = tuple(str(c) for c in tg.get("chat_ids") or [] if isinstance(c, str | int))
        events = frozenset(e for e in tg.get("events") or [] if e in NOTIFY_EVENTS)
        telegram = TelegramConfig(
            enabled=bool(tg.get("enabled")), token=token, chat_ids=chat_ids, events=events
        )
    return RuntimeSettings(
        sync_intervals=intervals,
        sync_overrides=overrides,
        phone_max_clients=phone_override or settings.PHONE_MAX_CLIENTS,
        phone_max_clients_override=phone_override,
        telegram=telegram,
        telegram_token_error=token_error,
    )


async def phone_max_clients(db: AsyncSession, settings: Settings) -> int:
    value = _int_or_none(
        await db.scalar(select(AppSetting.value).where(AppSetting.key == KEY_PHONE_MAX_CLIENTS))
    )
    return value if value is not None and 1 <= value <= 50 else settings.PHONE_MAX_CLIENTS


# --------------------------------------------------------------------------- admin API models


class TelegramUpdate(BaseModel):
    enabled: bool | None = None
    # write-only; "" clears the stored token
    bot_token: str | None = Field(None, max_length=128)
    chat_ids: list[str] | None = Field(None, max_length=20)
    events: list[NotifyEvent] | None = None

    @field_validator("bot_token")
    @classmethod
    def _token(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        v = v.strip()
        if not BOT_TOKEN_RE.match(v):
            raise ValueError("bot token must look like 123456:ABC... (from @BotFather)")
        return v

    @field_validator("chat_ids")
    @classmethod
    def _chats(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        out: list[str] = []
        for raw in v:
            c = raw.strip()
            if not CHAT_ID_RE.match(c):
                raise ValueError(f"invalid chat id: {c!r}")
            if c not in out:
                out.append(c)
        return out


class SettingsUpdate(BaseModel):
    # entity -> seconds; null removes the override (env default applies again)
    sync_intervals: dict[str, int | None] | None = None
    phone_max_clients: int | None = Field(None, ge=1, le=50)
    reset_phone_max_clients: bool = False
    telegram: TelegramUpdate | None = None

    @field_validator("sync_intervals")
    @classmethod
    def _intervals(cls, v: dict[str, int | None] | None) -> dict[str, int | None] | None:
        if v is None:
            return v
        for entity, seconds in v.items():
            if entity not in INTERVAL_BOUNDS:
                raise ValueError(f"unknown sync entity: {entity}")
            if seconds is not None:
                lo, hi = INTERVAL_BOUNDS[entity]
                if not lo <= seconds <= hi:
                    raise ValueError(f"{entity} interval must be between {lo} and {hi} seconds")
        return v


async def _put(db: AsyncSession, key: str, value: Any, admin_id: int | None) -> None:
    values = {"value": value, "updated_by": admin_id, "updated_at": clock.utcnow()}
    await db.execute(
        pg_insert(AppSetting)
        .values(key=key, **values)
        .on_conflict_do_update(index_elements=[AppSetting.key], set_=values)
    )


async def _delete(db: AsyncSession, key: str) -> None:
    row = await db.get(AppSetting, key)
    if row is not None:
        await db.delete(row)


async def apply_update(
    db: AsyncSession, settings: Settings, body: SettingsUpdate, admin_id: int | None
) -> None:
    """Persist a partial update (caller commits and audits).

    Serialised with a transaction-level advisory lock: the JSON values are read-merged-written,
    so two admins saving different fields at once must not lose one of the changes.
    """
    await db.execute(select(func.pg_advisory_xact_lock(SETTINGS_LOCK_KEY)))
    raw = await _raw(db)
    if body.sync_intervals is not None:
        current = raw.get(KEY_SYNC_INTERVALS)
        merged: dict[str, int] = dict(current) if isinstance(current, dict) else {}
        for entity, seconds in body.sync_intervals.items():
            if seconds is None:
                merged.pop(entity, None)
            else:
                merged[entity] = seconds
        if merged:
            await _put(db, KEY_SYNC_INTERVALS, merged, admin_id)
        else:
            await _delete(db, KEY_SYNC_INTERVALS)
    if body.reset_phone_max_clients:
        await _delete(db, KEY_PHONE_MAX_CLIENTS)
    elif body.phone_max_clients is not None:
        await _put(db, KEY_PHONE_MAX_CLIENTS, body.phone_max_clients, admin_id)
    if body.telegram is not None:
        tg_raw = raw.get(KEY_TELEGRAM)
        tg: dict[str, Any] = dict(tg_raw) if isinstance(tg_raw, dict) else {}
        t = body.telegram
        if t.enabled is not None:
            tg["enabled"] = t.enabled
        if t.chat_ids is not None:
            tg["chat_ids"] = t.chat_ids
        if t.events is not None:
            tg["events"] = sorted(set(t.events))
        if t.bot_token is not None:
            if t.bot_token == "":
                tg.pop("bot_token_enc", None)
            else:
                tg["bot_token_enc"] = encrypt_secret(settings, t.bot_token)
        tg.setdefault("events", list(NOTIFY_EVENTS))
        await _put(db, KEY_TELEGRAM, tg, admin_id)


def public_view(rt: RuntimeSettings, settings: Settings) -> dict[str, Any]:
    """Admin-facing representation; never includes the bot token."""
    defaults = env_intervals(settings)
    return {
        "sync_intervals": {
            e: {
                "default": defaults[e],
                "override": rt.sync_overrides.get(e),
                "effective": rt.sync_intervals[e],
                "min": INTERVAL_BOUNDS[e][0],
                "max": INTERVAL_BOUNDS[e][1],
            }
            for e in SYNC_ENTITIES
        },
        "phone_max_clients": {
            "default": settings.PHONE_MAX_CLIENTS,
            "override": rt.phone_max_clients_override,
            "effective": rt.phone_max_clients,
        },
        "telegram": {
            "enabled": rt.telegram.enabled,
            "token_configured": bool(rt.telegram.token),
            "token_error": rt.telegram_token_error,
            "chat_ids": list(rt.telegram.chat_ids),
            "events": sorted(rt.telegram.events),
            "active": rt.telegram.active,
        },
        "sync_enabled": settings.SYNC_ENABLED,
    }


def audit_view(body: SettingsUpdate) -> dict[str, Any]:
    """What changed, for the audit log (the token itself is never recorded)."""
    out = body.model_dump(exclude_none=True, exclude={"telegram"})
    if not body.reset_phone_max_clients:
        out.pop("reset_phone_max_clients", None)
    if body.telegram is not None:
        tg = body.telegram.model_dump(exclude_none=True, exclude={"bot_token"})
        if body.telegram.bot_token is not None:
            tg["bot_credential"] = "cleared" if body.telegram.bot_token == "" else "changed"
        out["telegram"] = tg
    return out

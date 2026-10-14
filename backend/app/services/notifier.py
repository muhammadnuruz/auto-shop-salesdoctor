"""Telegram notifications, driven by an outbox-style poll of order events and sync runs.

The worker calls :func:`tick` periodically. It reads ``shop_order_event`` rows (by id) and
finished ``sync_run`` rows (by ``(finished_at, id)``) after a cursor stored in ``app_setting``,
turns the interesting ones into messages and sends them. The order flow itself is untouched:
notifications can never slow down or break order submission.

Delivery rules:

* at-least-once per chat: progress of the message being delivered is kept per chat, so a chat
  that is rate limited never makes the others receive duplicates;
* a transient failure (network, 5xx, 429 — honouring ``retry_after``) or a bad bot token
  (401/404) blocks the queue and is retried later; a chat that rejects messages (400/403:
  wrong id, bot removed) is skipped for that message;
* messages older than :data:`STALE_AFTER` are dropped instead of flooding the chat after an
  outage; at most :data:`MAX_PER_TICK` messages per tick (Telegram group limit ~20/min);
* order events are re-scanned over a short id lookback with a dedupe set, and sync runs are
  read with a small lag, so rows that commit out of order are not missed;
* the last delivery result is kept as ``health`` and shown on the settings page.

The bot token is never logged or stored in plain text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.core.logging import get_logger
from app.models import AccessLink, AppSetting, SdClient, ShopOrder, ShopOrderEvent, SyncRun
from app.services import app_settings
from app.services.app_settings import KEY_TELEGRAM_CURSOR, TelegramConfig
from app.sync.common import SD_TZ

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"
BATCH = 50
MAX_PER_TICK = 10  # ticks run twice a minute -> <= 20 messages/min per chat
SEND_TIMEOUT = 10.0
STALE_AFTER = timedelta(hours=6)
SYNC_LAG = timedelta(seconds=5)  # sync_run.finished_at is set just before its commit
EVENT_LOOKBACK = 500  # ids; events committing out of id order are still picked up
MAX_RETRY_AFTER = 3600
TICK_BUDGET = 40.0  # seconds; well below the notify lock TTL and DB idle timeouts
ORDER_EVENTS = ("sd_ok", "sd_error", "outcome_unknown", "reconciled")
EXHAUSTED_MESSAGE = "reconcile attempts exhausted"


@dataclass(frozen=True)
class Notice:
    kind: str  # one of app_settings.NOTIFY_EVENTS
    text: str


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@dataclass
class Cursor:
    event_id: int  # high-water mark of processed order events
    sync_at: datetime | None
    sync_id: int
    recent_events: list[int] = field(default_factory=list)  # processed ids in the lookback
    # message ref ("e:<id>" / "s:<id>") -> chats that already received it; an entry only
    # disappears when that message is finished, whatever else is processed in between
    progress: dict[str, list[str]] = field(default_factory=dict)
    retry_until: datetime | None = None
    health: dict[str, Any] = field(default_factory=dict)

    def dump(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sync_at": _iso(self.sync_at),
            "sync_id": self.sync_id,
            "recent_events": self.recent_events[-EVENT_LOOKBACK:],
            "progress": self.progress,
            "retry_until": _iso(self.retry_until),
            "health": self.health,
        }

    @classmethod
    def load(cls, raw: Any) -> Cursor | None:
        if not isinstance(raw, dict):
            return None
        try:
            return cls(
                event_id=int(raw.get("event_id") or 0),
                sync_at=_parse(raw.get("sync_at")),
                sync_id=int(raw.get("sync_id") or 0),
                recent_events=[int(i) for i in raw.get("recent_events") or []],
                progress={
                    str(k): [str(c) for c in v]
                    for k, v in (raw.get("progress") or {}).items()
                    if isinstance(v, list)
                },
                retry_until=_parse(raw.get("retry_until")),
                health=dict(raw.get("health") or {}),
            )
        except (TypeError, ValueError):
            return None

    def mark_event(self, ev_id: int) -> None:
        self.event_id = max(self.event_id, ev_id)
        self.recent_events.append(ev_id)
        floor = self.event_id - EVENT_LOOKBACK
        self.recent_events = sorted({i for i in self.recent_events if i > floor})


def _money(value: Decimal | None) -> str:
    return f"{int(value or 0):,}".replace(",", " ")


def _local(ts: datetime | None) -> str:
    return ts.astimezone(SD_TZ).strftime("%d.%m %H:%M") if ts else ""


# --------------------------------------------------------------------------- collection


async def _current_cursor(db: AsyncSession) -> Cursor:
    event_id = await db.scalar(select(ShopOrderEvent.id).order_by(ShopOrderEvent.id.desc()))
    last_run = (
        await db.execute(
            select(SyncRun.finished_at, SyncRun.id)
            .where(SyncRun.finished_at.is_not(None))
            .order_by(SyncRun.finished_at.desc(), SyncRun.id.desc())
            .limit(1)
        )
    ).first()
    high = event_id or 0
    recent = (
        await db.scalars(select(ShopOrderEvent.id).where(ShopOrderEvent.id > high - EVENT_LOOKBACK))
    ).all()
    return Cursor(
        event_id=high,
        sync_at=last_run[0] if last_run else None,
        sync_id=last_run[1] if last_run else 0,
        recent_events=sorted(recent),
    )


def _order_notice(
    ev: ShopOrderEvent, order: ShopOrder, client_name: str | None, slug: str | None
) -> Notice | None:
    payload = ev.payload or {}
    head = f"{order.code_1c} · {client_name or '—'}"
    tail = f"Сумма: {_money(order.total_sum)} · ссылка: {slug or '—'}"
    if ev.event == "sd_ok" or (ev.event == "reconciled" and payload.get("op") == "submit"):
        return Notice("new_order", f"🆕 Новый заказ\n{head}\n{tail}")
    if ev.event == "outcome_unknown":
        return Notice(
            "outcome_unknown",
            f"⚠️ Результат отправки неизвестен (проверяется автоматически)\n{head}\n{tail}",
        )
    if ev.event == "sd_error":
        message = str(payload.get("message") or "")[:300]
        if message == EXHAUSTED_MESSAGE:
            return Notice(
                "reconcile_exhausted",
                f"🛑 Заказ требует проверки оператором (SD не подтвердил)\n{head}\n{tail}",
            )
        errors = payload.get("errors")
        detail = message or (str(errors)[:300] if errors else "")
        op = payload.get("op")
        what = "Ошибка заказа" if op in (None, "submit") else f"Ошибка операции «{op}»"
        return Notice(
            "order_failed", f"❌ {what}\n{head}\n{tail}" + (f"\n{detail}" if detail else "")
        )
    return None


@dataclass
class Item:
    ref: str  # "e:<event id>" / "s:<sync run id>"
    notice: Notice | None  # None: nothing to send, only advance the cursor
    at: datetime | None
    ev_id: int | None = None
    sync_key: tuple[datetime, int] | None = None


async def _order_items(db: AsyncSession, cursor: Cursor) -> list[Item]:
    floor = max(0, cursor.event_id - EVENT_LOOKBACK)
    q = (
        select(ShopOrderEvent, ShopOrder, SdClient.name, AccessLink.slug)
        .join(ShopOrder, ShopOrder.id == ShopOrderEvent.order_id)
        .join(SdClient, SdClient.id == ShopOrder.client_id)
        .join(AccessLink, AccessLink.id == ShopOrder.link_id)
        .where(ShopOrderEvent.id > floor, ShopOrderEvent.event.in_(ORDER_EVENTS))
    )
    if cursor.recent_events:
        q = q.where(ShopOrderEvent.id.not_in(cursor.recent_events))
    rows = (await db.execute(q.order_by(ShopOrderEvent.id).limit(BATCH))).all()
    return [
        Item(f"e:{ev.id}", _order_notice(ev, order, name, slug), ev.at, ev_id=ev.id)
        for ev, order, name, slug in rows
    ]


async def _previous_status(db: AsyncSession, run: SyncRun, finished_at: datetime) -> str | None:
    status: str | None = await db.scalar(
        select(SyncRun.status)
        .where(
            SyncRun.entity == run.entity,
            SyncRun.finished_at.is_not(None),
            tuple_(SyncRun.finished_at, SyncRun.id) < tuple_(finished_at, run.id),
        )
        .order_by(SyncRun.finished_at.desc(), SyncRun.id.desc())
        .limit(1)
    )
    return status


async def _sync_items(db: AsyncSession, cursor: Cursor, now: datetime) -> list[Item]:
    q = select(SyncRun).where(
        SyncRun.finished_at.is_not(None), SyncRun.finished_at < now - SYNC_LAG
    )
    if cursor.sync_at is not None:
        q = q.where(
            tuple_(SyncRun.finished_at, SyncRun.id) > tuple_(cursor.sync_at, cursor.sync_id)
        )
    runs = (await db.scalars(q.order_by(SyncRun.finished_at, SyncRun.id).limit(BATCH))).all()
    out: list[Item] = []
    for run in runs:
        finished = run.finished_at
        if finished is None:
            continue
        notice: Notice | None = None
        if run.status in ("ok", "error"):
            prev = await _previous_status(db, run, finished)
            if run.status == "error" and prev != "error":
                # only the transition into failure is reported, not every failing run
                notice = Notice(
                    "sync_error",
                    f"🔴 Ошибка синхронизации «{run.entity}» ({_local(finished)})\n"
                    + (run.error or "")[:300],
                )
            elif run.status == "ok" and prev == "error":
                notice = Notice(
                    "sync_error",
                    f"🟢 Синхронизация «{run.entity}» восстановлена ({_local(finished)})",
                )
        out.append(Item(f"s:{run.id}", notice, finished, sync_key=(finished, run.id)))
    return out


# --------------------------------------------------------------------------- delivery


class DeliveryError(Exception):
    """Blocking failure (network, 5xx, 429, bad token): retry the same message later."""

    def __init__(self, reason: str, retry_after: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


class PermanentDeliveryError(Exception):
    """This chat refused the message (wrong chat id, bot removed): skip the chat."""


def _retry_after(r: httpx.Response) -> int | None:
    try:
        value = r.json().get("parameters", {}).get("retry_after")
    except (ValueError, AttributeError):
        return None
    return min(int(value), MAX_RETRY_AFTER) if isinstance(value, int) and value > 0 else None


async def send_message(http: httpx.AsyncClient, token: str, chat_id: str, text: str) -> None:
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    try:
        r = await http.post(
            url,
            json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=SEND_TIMEOUT,
        )
    except httpx.HTTPError as exc:  # message may contain the URL -> keep the type only
        raise DeliveryError(type(exc).__name__) from None
    if r.status_code == 429:
        raise DeliveryError("rate limited (429)", _retry_after(r))
    if r.status_code >= 500:
        raise DeliveryError(f"http {r.status_code}")
    if r.status_code in (401, 404):  # the token itself is wrong / revoked
        raise DeliveryError(f"bot token rejected (http {r.status_code})")
    if r.status_code >= 400:
        log.warning("telegram_rejected", status=r.status_code, chat=chat_id)
        raise PermanentDeliveryError(f"http {r.status_code}")


async def send_test(http: httpx.AsyncClient, cfg: TelegramConfig) -> dict[str, str]:
    """Admin "send test message": per-chat result ("ok" or an error class, never the token)."""
    results: dict[str, str] = {}
    if not cfg.token:
        return results
    for chat in cfg.chat_ids:
        try:
            await send_message(http, cfg.token, chat, "✅ Auto-Shop: тестовое уведомление")
            results[chat] = "ok"
        except (DeliveryError, PermanentDeliveryError) as exc:
            results[chat] = f"error: {exc}"
    return results


async def _save_cursor(db: AsyncSession, cursor: Cursor) -> None:
    row = await db.get(AppSetting, KEY_TELEGRAM_CURSOR)
    if row is None:
        db.add(AppSetting(key=KEY_TELEGRAM_CURSOR, value=cursor.dump()))
    else:
        row.value = cursor.dump()
    await db.commit()


async def health(db: AsyncSession) -> dict[str, Any]:
    """Last delivery outcome (for the settings page)."""
    row = await db.get(AppSetting, KEY_TELEGRAM_CURSOR)
    cursor = Cursor.load(row.value if row else None)
    if cursor is None:
        return {}
    out = dict(cursor.health)
    out["retry_until"] = _iso(cursor.retry_until)
    return out


def _advance(cursor: Cursor, item: Item) -> None:
    if item.ev_id is not None:
        cursor.mark_event(item.ev_id)
    if item.sync_key is not None:
        cursor.sync_at, cursor.sync_id = item.sync_key
    cursor.progress.pop(item.ref, None)


async def _deliver(
    http: httpx.AsyncClient, cfg: TelegramConfig, cursor: Cursor, item: Item, text: str
) -> None:
    """Send to every chat not yet served for this item; raises DeliveryError to block."""
    token = cfg.token or ""  # cfg.active guarantees a token
    done = cursor.progress.setdefault(item.ref, [])
    for chat in cfg.chat_ids:
        if chat in done:
            continue
        try:
            await send_message(http, token, chat, text)
        except PermanentDeliveryError as exc:
            cursor.health.update(
                last_error=f"chat {chat}: {exc}", last_error_at=_iso(clock.utcnow())
            )
        done.append(chat)


async def tick(db: AsyncSession, http: httpx.AsyncClient, settings: Settings) -> int:
    """One notifier pass; returns the number of messages delivered."""
    rt = await app_settings.load(db, settings)
    stored = await db.get(AppSetting, KEY_TELEGRAM_CURSOR)
    cursor = Cursor.load(stored.value if stored else None)
    if cursor is None:  # first run: start from "now", never replay history
        await _save_cursor(db, await _current_cursor(db))
        return 0
    now = clock.utcnow()
    cfg = rt.telegram
    if cfg.active and cursor.retry_until is not None and cursor.retry_until > now:
        return 0  # honouring Telegram's retry_after / back-off

    items = await _order_items(db, cursor) + await _sync_items(db, cursor, now)
    # partially delivered messages first, so their progress is never lost or redone
    items.sort(key=lambda it: it.ref not in cursor.progress)
    await db.commit()  # no transaction stays open across Telegram calls
    started = time.monotonic()
    sent = 0
    for item in items:
        notice = item.notice
        wanted = (
            cfg.active
            and notice is not None
            and notice.kind in cfg.events
            and (item.at is None or now - item.at <= STALE_AFTER)
        )
        if not wanted or notice is None:
            _advance(cursor, item)
            continue
        if sent >= MAX_PER_TICK or time.monotonic() - started > TICK_BUDGET:
            break  # the rest goes out on the next tick
        try:
            await _deliver(http, cfg, cursor, item, notice.text)
        except DeliveryError as exc:
            log.warning("telegram_delivery_failed", error=exc.reason)
            backoff = exc.retry_after or 30
            cursor.retry_until = now + timedelta(seconds=backoff)
            cursor.health.update(last_error=exc.reason, last_error_at=_iso(now))
            break
        sent += 1
        cursor.retry_until = None
        cursor.health["last_ok_at"] = _iso(now)
        _advance(cursor, item)
        await _save_cursor(db, cursor)  # a crash later never re-sends this one
    await _save_cursor(db, cursor)
    return sent

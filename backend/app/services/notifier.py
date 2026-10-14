"""Telegram notifications, driven by an outbox-style poll of order events and sync runs.

The worker calls :func:`tick` periodically. It reads new ``shop_order_event`` rows (by id) and
finished ``sync_run`` rows (by ``(finished_at, id)``) after a cursor stored in ``app_setting``,
turns the interesting ones into messages and sends them. The order flow itself is untouched:
notifications can never slow down or break order submission.

Delivery is at-least-once: the cursor only advances past a message once every chat accepted
it (or rejected it permanently, e.g. a wrong chat id). The bot token is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import get_logger
from app.models import AccessLink, AppSetting, SdClient, ShopOrder, ShopOrderEvent, SyncRun
from app.services import app_settings
from app.services.app_settings import KEY_TELEGRAM_CURSOR, TelegramConfig
from app.sync.common import SD_TZ

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org"
BATCH = 50
SEND_TIMEOUT = 10.0
ORDER_EVENTS = ("sd_ok", "sd_error", "outcome_unknown", "reconciled")
EXHAUSTED_MESSAGE = "reconcile attempts exhausted"


@dataclass(frozen=True)
class Notice:
    kind: str  # one of app_settings.NOTIFY_EVENTS
    text: str


@dataclass
class Cursor:
    event_id: int
    sync_at: datetime | None
    sync_id: int

    def dump(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sync_at": self.sync_at.isoformat() if self.sync_at else None,
            "sync_id": self.sync_id,
        }

    @classmethod
    def load(cls, raw: Any) -> Cursor | None:
        if not isinstance(raw, dict):
            return None
        try:
            at = raw.get("sync_at")
            return cls(
                event_id=int(raw.get("event_id") or 0),
                sync_at=datetime.fromisoformat(at) if at else None,
                sync_id=int(raw.get("sync_id") or 0),
            )
        except (TypeError, ValueError):
            return None


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
    return Cursor(
        event_id=event_id or 0,
        sync_at=last_run[0] if last_run else None,
        sync_id=last_run[1] if last_run else 0,
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


async def _order_notices(db: AsyncSession, cursor: Cursor) -> tuple[list[tuple[Notice, int]], int]:
    rows = (
        await db.execute(
            select(ShopOrderEvent, ShopOrder, SdClient.name, AccessLink.slug)
            .join(ShopOrder, ShopOrder.id == ShopOrderEvent.order_id)
            .join(SdClient, SdClient.id == ShopOrder.client_id)
            .join(AccessLink, AccessLink.id == ShopOrder.link_id)
            .where(ShopOrderEvent.id > cursor.event_id, ShopOrderEvent.event.in_(ORDER_EVENTS))
            .order_by(ShopOrderEvent.id)
            .limit(BATCH)
        )
    ).all()
    out: list[tuple[Notice, int]] = []
    for ev, order, client_name, slug in rows:
        notice = _order_notice(ev, order, client_name, slug)
        if notice is not None:
            out.append((notice, ev.id))
    last = rows[-1][0].id if rows else cursor.event_id
    return out, last


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


async def _sync_notices(
    db: AsyncSession, cursor: Cursor
) -> tuple[list[tuple[Notice, tuple[datetime, int]]], tuple[datetime | None, int]]:
    q = select(SyncRun).where(SyncRun.finished_at.is_not(None))
    if cursor.sync_at is not None:
        q = q.where(
            tuple_(SyncRun.finished_at, SyncRun.id) > tuple_(cursor.sync_at, cursor.sync_id)
        )
    runs = (await db.scalars(q.order_by(SyncRun.finished_at, SyncRun.id).limit(BATCH))).all()
    out: list[tuple[Notice, tuple[datetime, int]]] = []
    for run in runs:
        finished = run.finished_at
        if finished is None or run.status not in ("ok", "error"):
            continue
        prev = await _previous_status(db, run, finished)
        if run.status == "error" and prev != "error":
            # only the transition into failure is reported, not every failing run
            text = (
                f"🔴 Ошибка синхронизации «{run.entity}» ({_local(finished)})\n"
                + (run.error or "")[:300]
            )
            out.append((Notice("sync_error", text), (finished, run.id)))
        elif run.status == "ok" and prev == "error":
            text = f"🟢 Синхронизация «{run.entity}» восстановлена ({_local(finished)})"
            out.append((Notice("sync_error", text), (finished, run.id)))
    if runs:
        last = runs[-1]
        return out, (last.finished_at, last.id)
    return out, (cursor.sync_at, cursor.sync_id)


# --------------------------------------------------------------------------- delivery


class DeliveryError(Exception):
    """Transient failure (network, 429, 5xx): retry on the next tick."""


async def send_message(http: httpx.AsyncClient, token: str, chat_id: str, text: str) -> None:
    """Send one message. Raises DeliveryError on transient failures; returns quietly after
    logging on permanent ones (bad chat id, bot blocked) so the queue is not stuck forever."""
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    try:
        r = await http.post(
            url,
            json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=SEND_TIMEOUT,
        )
    except httpx.HTTPError as exc:  # message may contain the URL -> log the type only
        raise DeliveryError(type(exc).__name__) from None
    if r.status_code == 429 or r.status_code >= 500:
        raise DeliveryError(f"http {r.status_code}")
    if r.status_code >= 400:
        log.warning("telegram_rejected", status=r.status_code, chat=chat_id)
        raise PermanentDeliveryError(f"http {r.status_code}")


class PermanentDeliveryError(Exception):
    """Telegram refused the message (wrong chat id / token, bot blocked)."""


async def deliver(http: httpx.AsyncClient, cfg: TelegramConfig, text: str) -> None:
    if not cfg.token:
        return
    for chat in cfg.chat_ids:
        try:
            await send_message(http, cfg.token, chat, text)
        except PermanentDeliveryError:
            continue


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


async def tick(db: AsyncSession, http: httpx.AsyncClient, settings: Settings) -> int:
    """One notifier pass; returns the number of messages delivered."""
    rt = await app_settings.load(db, settings)
    stored = await db.get(AppSetting, KEY_TELEGRAM_CURSOR)
    cursor = Cursor.load(stored.value if stored else None)
    if cursor is None:  # first run: start from "now", never replay history
        await _save_cursor(db, await _current_cursor(db))
        return 0

    cfg = rt.telegram
    order_items, order_last = await _order_notices(db, cursor)
    sync_items, sync_last = await _sync_notices(db, cursor)
    sent = 0
    if cfg.active:
        for notice, ev_id in order_items:
            if notice.kind in cfg.events:
                try:
                    await deliver(http, cfg, notice.text)
                except DeliveryError as exc:
                    log.warning("telegram_delivery_failed", error=str(exc))
                    await _save_cursor(db, cursor)  # retried from this event next tick
                    return sent
                sent += 1
            cursor.event_id = ev_id
        for notice, (at, run_id) in sync_items:
            if notice.kind in cfg.events:
                try:
                    await deliver(http, cfg, notice.text)
                except DeliveryError as exc:
                    log.warning("telegram_delivery_failed", error=str(exc))
                    cursor.event_id = order_last
                    await _save_cursor(db, cursor)
                    return sent
                sent += 1
            cursor.sync_at, cursor.sync_id = at, run_id
    cursor.event_id = order_last
    cursor.sync_at, cursor.sync_id = sync_last
    await _save_cursor(db, cursor)
    return sent

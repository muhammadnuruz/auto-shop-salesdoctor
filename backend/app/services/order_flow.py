"""Order <-> Sales Doctor flow: submit, cancel, admin edit/status/delete, reconcile.

Principles
* ``code_1C`` ("WEB-000123") makes ``setOrder`` idempotent, so re-sending the same payload is
  always safe; the only hazard is double-counting our own reservation when re-validating stock
  (hence the "own reservation" credit).
* EVERY SD-touching path (submit, cancel, admin edit/status/delete, reconcile, status sync) runs
  under the same per-order Redis lock (``order_lock``/``try_lock``). After acquiring it the row
  is re-read ``FOR UPDATE`` and state + version are re-checked; writes bump ``version`` with a
  guarded ``UPDATE ... WHERE version = :v``. The lock is renewed before each SD call.
* No DB transaction is held across an SD call, except in ``admin_edit`` which must keep local and
  SD state consistent (admin-only, low contention).
* When an SD write has an unknown outcome the order becomes ``outcome_unknown`` and records the
  pending operation (``last_error.op``: submit | admin_edit | cancel | status | delete). Reconcile
  is operation-aware: finding the order in SD by code_1C is NOT proof that the latest write
  landed, so it compares status/lines and re-sends the current payload when needed.
"""

import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import LockError, RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.logging import get_logger
from app.domain.link_settings import LinkSettings
from app.integrations.salesdoctor import SalesDoctorClient
from app.integrations.salesdoctor.errors import (
    SDError,
    SDOutcomeUnknownError,
    SDRowError,
    SDValidationError,
)
from app.integrations.salesdoctor.schemas import (
    EntityID,
    Order,
    OrderIn,
    OrderProductIn,
    OrderStatusIn,
    SetResult,
)
from app.models import AccessLink, SdClient, ShopOrder
from app.services import audit
from app.services.eligibility import parse_settings
from app.services.jobs import RECONCILE_ORDER, REFRESH_WAREHOUSE_STOCK, JobQueue
from app.services.orders import (
    EDITABLE_STATES,
    Actor,
    HeaderOptions,
    LineView,
    OrderView,
    add_event,
    build_view,
    bump_version,
    check_version,
    collect_problems,
    get_order,
    problem,
)
from app.services.stock_fresh import apply_to_mirror, fetch_warehouse_stock
from app.sync.common import SD_TZ

log = get_logger(__name__)

SD_DT = "%Y-%m-%d %H:%M:%S"
LOCK_TTL = 600  # worst case: stock read + several SD calls with retries; renewed per call
RECONCILE_MIN_AGE = timedelta(minutes=2)
STUCK_SUBMITTING_AFTER = timedelta(minutes=5)
RECONCILE_MAX_ATTEMPTS = 6
RECONCILE_BASE_DELAY = 60.0
RECONCILE_MAX_DELAY = 3600.0

SYSTEM = Actor("system")

PushKind = Literal["sent", "rejected", "unknown", "unavailable"]


@dataclass
class PushResult:
    kind: PushKind
    sd_id: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    message: str | None = None


@dataclass
class Services:
    """What the flow needs from the outside world (request- or worker-scoped)."""

    sd: SalesDoctorClient
    redis: Redis
    jobs: JobQueue


# ---------------------------------------------------------------------------------- payloads


def _ref(sd_id: str) -> EntityID:
    return EntityID(SD_id=sd_id)


def build_payload(
    order: ShopOrder, views: list[LineView], client: SdClient, status: int
) -> OrderIn:
    """The setOrder body. Prices are the server-side stored (mirror) prices, never client input."""
    raw_trade = order.trade_sd_id or ""
    # SD returns/accepts trade directions as numbers (verified live)
    trade: str | int = int(raw_trade) if raw_trade.isdigit() else raw_trade
    return OrderIn(
        code_1C=order.code_1c,
        status=status,
        comment=order.comment or "",
        client=_ref(client.sd_id),
        agent=_ref(order.agent_sd_id or ""),
        priceType=_ref(order.price_type_sd_id or ""),
        warehouse=_ref(order.warehouse_sd_id or ""),
        trade=EntityID(SD_id=trade),
        orderProducts=[
            OrderProductIn(
                product=_ref(v.product.sd_id),
                quantity=float(v.line.quantity),
                price=float(v.line.price),
                discountSumma=0,
            )
            for v in views
        ],
    )


def map_row_errors(rows: list[SDRowError], product_refs: list[str]) -> list[dict[str, Any]]:
    """Per-message errors; mapped to a product when SD points at an order-product row."""
    out: list[dict[str, Any]] = []
    for row in rows:
        entity = row.entity.lower()
        line_level = entity.startswith(("orderproduct", "product")) and row.index is not None
        idx = row.index
        ref = (
            product_refs[idx]
            if line_level and idx is not None and 0 <= idx < len(product_refs)
            else None
        )
        for message in row.messages or ["Rejected by Sales Doctor"]:
            out.append({"product_ref": ref, "message": message[:500], "entity": row.entity})
    return out


async def push_order(sd: SalesDoctorClient, payload: OrderIn) -> PushResult:
    """Send one order; classifies the outcome without touching the database."""
    refs = [p.product.sd_id or "" for p in payload.orderProducts]
    try:
        res: SetResult = await sd.set_orders([payload], retry=True)
    except SDOutcomeUnknownError as exc:
        return PushResult("unknown", message=exc.message)
    except SDValidationError as exc:
        return PushResult("rejected", errors=map_row_errors(exc.items, refs), message=exc.message)
    except SDError as exc:  # transport/auth/protocol: the request provably did not apply
        return PushResult("unavailable", message=exc.message)
    if not res.ok:
        errors = map_row_errors(res.row_errors, refs)
        return PushResult("rejected", errors=errors, message="Sales Doctor rejected the order")
    items = res.ids("order")
    return PushResult("sent", sd_id=items[0].SD_id if items else None)


async def push_status(
    sd: SalesDoctorClient, order: ShopOrder, status: int, shipment_date: str | None = None
) -> PushResult:
    change = OrderStatusIn(
        code_1C=order.code_1c, SD_id=order.sd_id or "", status=status, dateShipment=shipment_date
    )
    try:
        res = await sd.set_order_status([change], retry=True)
    except SDOutcomeUnknownError as exc:
        return PushResult("unknown", message=exc.message)
    except SDValidationError as exc:
        return PushResult("rejected", errors=map_row_errors(exc.items, []), message=exc.message)
    except SDError as exc:
        return PushResult("unavailable", message=exc.message)
    if not res.ok:
        return PushResult(
            "rejected",
            errors=map_row_errors(res.row_errors, []),
            message="Rejected by Sales Doctor",
        )
    return PushResult("sent", sd_id=order.sd_id)


# ------------------------------------------------------------------------------- state helpers


def _error_doc(code: str, result: PushResult, **extra: Any) -> dict[str, Any]:
    return {
        "code": code,
        "message": result.message,
        "errors": result.errors,
        "at": clock.utcnow().isoformat(),
        **extra,
    }


def mark_sent(order: ShopOrder, result: PushResult, status: int) -> None:
    now = clock.utcnow()
    order.state = "sent"
    order.sd_id = result.sd_id or order.sd_id
    order.sd_status = status
    order.sent_at = order.sent_at or now
    order.sd_synced_at = now
    order.last_error = None


def mark_failed(order: ShopOrder, result: PushResult, code: str) -> None:
    order.state = "failed"
    order.last_error = _error_doc(code, result)


def op_doc(name: str, **extra: Any) -> dict[str, Any]:
    """The pending operation recorded while the SD outcome is unknown."""
    return {"name": name, **{k: v for k, v in extra.items() if v is not None}}


def payload_digest(payload: OrderIn) -> str:
    body = json.dumps(payload.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def pending_op(order: ShopOrder) -> dict[str, Any] | None:
    op = (order.last_error or {}).get("op")
    return dict(op) if isinstance(op, dict) else None


def mark_unknown(
    order: ShopOrder,
    result: PushResult | None,
    attempts: int = 0,
    op: dict[str, Any] | None = None,
    *,
    exhausted: bool = False,
) -> None:
    now = clock.utcnow()
    delay = RECONCILE_MIN_AGE.total_seconds() if attempts == 0 else _reconcile_delay(attempts)
    doc: dict[str, Any] = {
        "code": "outcome_unknown",
        "message": result.message if result else "Unexpected error while sending",
        "errors": [],
        "at": now.isoformat(),
        "reconcile_attempts": attempts,
        "op": op or pending_op(order),
    }
    if exhausted:
        doc["exhausted"] = True  # parked: needs an operator (admin resend), no more auto retries
    else:
        doc["next_attempt_at"] = (now + timedelta(seconds=delay)).isoformat()
    order.state = "outcome_unknown"
    order.last_error = doc


def order_status_for(order: ShopOrder, st: LinkSettings | None) -> int:
    return order.sd_status or (st.order_status if st is not None else 1)


def apply_sd_state(order: ShopOrder, sd_id: str | None, status: int | None) -> bool:
    """Mirror SD's id/status onto our order (5 -> cancelled, back to sent otherwise).

    Returns True when something changed. ``outcome_unknown`` orders are never touched here:
    only reconcile may decide whether the pending write landed.
    """
    if order.state == "outcome_unknown":
        return False
    changed = False
    if sd_id and order.sd_id != sd_id:
        order.sd_id = sd_id
        changed = True
    if status is not None and order.sd_status != status:
        order.sd_status = status
        if status == 5 and order.state == "sent":
            order.state = "cancelled"
        elif status != 5 and order.state == "cancelled":
            order.state = "sent"
        changed = True
    order.sd_synced_at = clock.utcnow()
    return changed


def record_audit(
    db: AsyncSession,
    actor: Actor,
    action: str,
    order: ShopOrder,
    meta: audit.RequestMeta | None,
    before: Any = None,
    after: Any = None,
) -> None:
    audit.record(
        db,
        actor_type=actor.type,
        actor_id=actor.id,
        action=action,
        entity_type="shop_order",
        entity_id=order.id,
        before=before,
        after={"code": order.code_1c, "public_id": str(order.public_id), **(after or {})},
        meta=meta,
    )


# ------------------------------------------------------------------------------------- locking


class OrderLock:
    """Per-order Redis lock; ``renew`` pushes the TTL out again before each SD call."""

    def __init__(self, lock: Any) -> None:
        self._lock = lock

    async def renew(self) -> None:
        try:
            await self._lock.extend(LOCK_TTL, replace_ttl=True)
        except (LockError, RedisError) as exc:
            log.warning("order_lock_renew_failed", error=type(exc).__name__)

    async def release(self) -> None:
        try:
            await self._lock.release()
        except (LockError, RedisError) as exc:
            log.warning("order_lock_release_failed", error=type(exc).__name__)


def _lock_key(order_id: int) -> str:
    return f"order:lock:{order_id}"


async def try_lock(redis: Redis, order_id: int) -> OrderLock | None:
    """Non-blocking acquire; None when another operation holds it (or Redis is down)."""
    lock = redis.lock(_lock_key(order_id), timeout=LOCK_TTL, blocking=False)
    try:
        if not await lock.acquire():
            return None
    except RedisError:
        return None
    return OrderLock(lock)


async def _acquire(redis: Redis, order_id: int) -> OrderLock:
    lock = redis.lock(_lock_key(order_id), timeout=LOCK_TTL, blocking=False)
    try:
        acquired = await lock.acquire()
    except RedisError as exc:
        raise HTTPException(503, "Service temporarily unavailable") from exc
    if not acquired:
        raise problem(409, "order_busy", message="Another operation on this order is in progress")
    return OrderLock(lock)


@asynccontextmanager
async def order_lock(redis: Redis, order_id: int) -> AsyncIterator[OrderLock]:
    lock = await _acquire(redis, order_id)
    try:
        yield lock
    finally:
        await lock.release()


async def _parts(
    db: AsyncSession, order: ShopOrder
) -> tuple[SdClient, AccessLink, LinkSettings | None]:
    client = await db.get(SdClient, order.client_id)
    link = await db.get(AccessLink, order.link_id)
    if client is None or link is None:  # FKs are RESTRICT; cannot happen
        raise HTTPException(500, "Order references are missing")
    return client, link, parse_settings(link)


@dataclass
class FlowResult:
    kind: Literal["sent", "rejected", "unknown", "unavailable"]
    order: ShopOrder
    view: OrderView
    errors: list[dict[str, Any]] = field(default_factory=list)


REFRESH_BUCKET = 10  # seconds: sends of one warehouse within a bucket share one refresh


async def enqueue_stock_refresh(svc: Services, warehouse_sd_id: str | None) -> None:
    """Full-warehouse mirror refresh shortly after a send, deduplicated per time bucket (50
    submits in a burst trigger a handful of getStock calls, not 50)."""
    if not warehouse_sd_id:
        return
    bucket = int(time.time()) // REFRESH_BUCKET
    await svc.jobs.enqueue(
        REFRESH_WAREHOUSE_STOCK,
        warehouse_sd_id,
        defer_by=2,
        job_id=f"stock-refresh:{warehouse_sd_id}:{bucket}",
    )


async def _enqueue_reconcile(svc: Services, order_id: int) -> None:
    await svc.jobs.enqueue(RECONCILE_ORDER, order_id, defer_by=RECONCILE_MIN_AGE.total_seconds())


# ---------------------------------------------------------------------------- SD lookups


def _window(order: ShopOrder) -> tuple[str, str]:
    # SD stamps the order when WE sent it, so the window starts at the first send attempt
    start = (order.submitted_at or order.created_at or clock.utcnow()).astimezone(SD_TZ)
    start = start - timedelta(days=1)
    end = clock.utcnow().astimezone(SD_TZ) + timedelta(days=1)
    return (
        start.replace(hour=0, minute=0, second=0, microsecond=0).strftime(SD_DT),
        end.replace(hour=23, minute=59, second=59, microsecond=0).strftime(SD_DT),
    )


async def find_in_sd(sd: SalesDoctorClient, order: ShopOrder) -> Order | None:
    """The SD order carrying our code_1C (date-window scan; raises SDError on read failure)."""
    date_from, date_to = _window(order)
    async for o in sd.iterate_orders(date_from=date_from, date_to=date_to):
        if o.code_1C == order.code_1c:
            return o
    return None


def sd_lines(found: Order) -> dict[str, Decimal] | None:
    """{product SD_id: quantity} of an SD order; None when the rows cannot be understood."""
    out: dict[str, Decimal] = {}
    for row in found.orderProducts:
        product = row.get("product")
        ref = product.get("SD_id") if isinstance(product, dict) else row.get("SD_id")
        qty = row.get("quantity")
        if ref is None or qty is None:
            return None
        try:
            out[str(ref)] = out.get(str(ref), Decimal(0)) + Decimal(str(qty))
        except ArithmeticError:
            return None
    return out


def lines_match(found: Order, views: list[LineView]) -> bool:
    theirs = sd_lines(found)
    if theirs is None:
        return False  # cannot prove equality -> resend (idempotent)
    mine = {v.product.sd_id: v.line.quantity for v in views}
    return theirs == mine


async def _own_reservation_credit(
    sd: SalesDoctorClient, order: ShopOrder, pre: OrderView
) -> dict[int, Decimal]:
    """Stock our own earlier send already holds in SD (so a resubmit does not double-count it)."""
    try:
        found = await find_in_sd(sd, order)
    except SDError:
        return {}  # stricter, never looser: no credit
    if found is None or found.status == 5:
        return {}
    theirs = sd_lines(found) or {}
    return {v.product.id: theirs[v.product.sd_id] for v in pre.lines if v.product.sd_id in theirs}


# ------------------------------------------------------------------------------------- submit


async def submit_order(
    db: AsyncSession,
    svc: Services,
    *,
    public_id: uuid.UUID,
    actor: Actor,
    client_id: int | None,
    options_for: Any,  # async (client, link, st) -> HeaderOptions
    admin_mode: bool,
    expected_version: int | None,
    meta: audit.RequestMeta | None,
    link_id: int | None = None,
) -> FlowResult:
    """draft|failed -> submitting -> validate (fresh stock) -> setOrder -> sent|failed|unknown."""
    order = await get_order(db, public_id, client_id=client_id)
    if link_id is not None and order.link_id != link_id:
        raise problem(409, "order_other_link", message="Order belongs to another link")
    async with order_lock(svc.redis, order.id) as lock:
        return await _submit_locked(
            db, svc, lock, order.public_id, actor, options_for, admin_mode, expected_version, meta
        )


async def _submit_locked(
    db: AsyncSession,
    svc: Services,
    lock: OrderLock,
    public_id: uuid.UUID,
    actor: Actor,
    options_for: Any,
    admin_mode: bool,
    expected_version: int | None,
    meta: audit.RequestMeta | None,
) -> FlowResult:
    order = await get_order(db, public_id, for_update=True)
    if order.state not in EDITABLE_STATES:
        state = order.state
        await db.rollback()  # expires loaded attributes: read them first
        raise problem(409, "invalid_state", state=state)
    check_version(order, expected_version)
    client, link, st = await _parts(db, order)
    if st is None and not admin_mode:
        raise problem(409, "link_misconfigured", message="Link settings are invalid")
    options: HeaderOptions = await options_for(client, link, st)

    # cheap checks first (no SD round trip, no state change)
    pre = await build_view(db, order, st=st, admin=admin_mode)
    early = list(collect_problems(pre, options, client, admin=admin_mode, check_stock=False))
    if early:
        await db.rollback()
        raise problem(422, "validation_failed", errors=early)

    first_attempt = order.submitted_at is None
    prev_state = order.state
    order.state = "submitting"
    order.submitted_at = clock.utcnow()
    add_event(db, order, "submitted", actor, {"from": prev_state})
    await bump_version(db, order)
    await db.commit()

    async def revert(to: str) -> None:
        order.state = to
        await db.commit()

    # fresh stock for the warehouse (never trust the 60 s mirror at submit time)
    assert order.warehouse_sd_id is not None  # noqa: S101 (checked by collect_problems)
    await lock.renew()
    try:
        fresh = await fetch_warehouse_stock(svc.sd, order.warehouse_sd_id)
    except SDError as exc:
        await revert(prev_state)
        raise problem(
            503, "sd_unavailable", message="Sales Doctor is unavailable, try again"
        ) from exc
    except BaseException:
        await revert(prev_state)
        raise
    credit: dict[int, Decimal] = {}
    if not first_attempt:  # an earlier attempt may already hold stock in SD
        await lock.renew()
        credit = await _own_reservation_credit(svc.sd, order, pre)
    try:
        # state is "submitting" now, so ask for the live (repriced, stock-checked) view explicitly
        view = await build_view(
            db, order, st=st, admin=admin_mode, stock_override=fresh, credit=credit, live=True
        )
        if fresh is not None:
            # Only this order's products: this transaction stays open across the SD call, and
            # upserting the whole warehouse here would hold row locks on every sd_stock row,
            # serialising all concurrent submits. The full warehouse refresh runs as a job.
            mine = [v.product.sd_id for v in view.lines]
            await apply_to_mirror(
                db,
                order.warehouse_sd_id,
                {ref: fresh[ref] for ref in mine if ref in fresh},
                force_zero=mine,
            )
        problems = collect_problems(view, options, client, admin=admin_mode)
    except BaseException:
        await db.rollback()
        await revert(prev_state)
        raise
    if problems:
        order.state = "draft"
        await db.commit()
        raise problem(422, "validation_failed", errors=problems)

    status = st.order_status if st is not None else 1
    payload = build_payload(order, view.lines, client, status)
    # Nothing below rolls back (every outcome is recorded), so end the transaction before the
    # SD call: no row locks (sd_stock of this order's products) are held while SD answers.
    await db.commit()
    await lock.renew()
    try:
        result = await push_order(svc.sd, payload)
    except Exception:  # unexpected: the request may have been sent
        log.exception("order_push_crashed", order=order.code_1c)
        result = PushResult("unknown", message="Unexpected error while sending")

    if result.kind == "sent":
        mark_sent(order, result, status)
        add_event(db, order, "sd_ok", actor, {"sd_id": order.sd_id, "status": status})
        record_audit(db, actor, "order.submit", order, meta, after={"sd_id": order.sd_id})
    elif result.kind == "rejected":
        mark_failed(order, result, "sd_rejected")
        add_event(
            db, order, "sd_error", actor, {"errors": result.errors, "message": result.message}
        )
        record_audit(db, actor, "order.submit_rejected", order, meta)
    elif result.kind == "unavailable":
        mark_failed(order, result, "sd_unavailable")
        add_event(db, order, "sd_error", actor, {"message": result.message})
    else:
        mark_unknown(
            order,
            result,
            op=op_doc("submit", status=status, payload_hash=payload_digest(payload)),
        )
        add_event(db, order, "outcome_unknown", actor, {"message": result.message, "op": "submit"})
    await db.commit()

    if result.kind == "sent":
        await enqueue_stock_refresh(svc, order.warehouse_sd_id)
    elif result.kind == "unknown":
        await _enqueue_reconcile(svc, order.id)
    return FlowResult(result.kind, order, view, result.errors)


# ------------------------------------------------------------------------------------ cancel


async def cancel_order(
    db: AsyncSession,
    svc: Services,
    *,
    public_id: uuid.UUID,
    actor: Actor,
    client_id: int,
    expected_version: int | None,
    meta: audit.RequestMeta | None,
) -> FlowResult:
    """Shop cancel: only a sent order that is still "Новый" in SD. setOrder(status 5)."""
    order = await get_order(db, public_id, client_id=client_id)
    async with order_lock(svc.redis, order.id) as lock:
        order = await get_order(db, public_id, for_update=True)
        if order.state != "sent" or order.sd_status != 1:
            state, sd_status = order.state, order.sd_status
            await db.rollback()
            raise problem(409, "not_cancellable", state=state, sd_status=sd_status)
        check_version(order, expected_version)
        client, _link, st = await _parts(db, order)
        view = await build_view(db, order, st=st, live=False)
        await db.commit()  # release the row lock before talking to SD

        # the dealer may have shipped it meanwhile: verify the live SD status first
        await lock.renew()
        try:
            found = await find_in_sd(svc.sd, order)
        except SDError as exc:
            raise problem(503, "sd_unavailable", message="Cannot verify the order in SD") from exc
        if found is None:
            raise problem(409, "sd_order_not_found", message="Order was not found in SD")
        order = await get_order(db, public_id, for_update=True)
        if found.status != 1:
            if apply_sd_state(order, found.SD_id, found.status):
                add_event(db, order, "status_changed", SYSTEM, {"sd_status": found.status})
            state, sd_status = order.state, order.sd_status
            await db.commit()
            raise problem(409, "not_cancellable", state=state, sd_status=sd_status)
        await db.commit()

        payload = build_payload(order, view.lines, client, 5)
        await lock.renew()
        result = await push_order(svc.sd, payload)
        order = await get_order(db, public_id, for_update=True)
        if result.kind == "sent":
            order.state = "cancelled"
            order.sd_status = 5
            order.sd_synced_at = clock.utcnow()
            await bump_version(db, order)
            add_event(db, order, "cancelled", actor, {"by": actor.type})
            record_audit(db, actor, "order.cancel", order, meta)
        elif result.kind == "rejected":
            order.last_error = _error_doc("cancel_rejected", result)
            add_event(db, order, "sd_error", actor, {"errors": result.errors, "op": "cancel"})
        elif result.kind == "unknown":  # L1: do not leave it looking like a plain "sent"
            mark_unknown(order, result, op=op_doc("cancel", payload_hash=payload_digest(payload)))
            add_event(
                db, order, "outcome_unknown", actor, {"message": result.message, "op": "cancel"}
            )
        else:
            order.last_error = _error_doc("cancel_" + result.kind, result)
            add_event(db, order, "sd_error", actor, {"message": result.message, "op": "cancel"})
        await db.commit()
    if result.kind == "sent":
        await enqueue_stock_refresh(svc, order.warehouse_sd_id)
    elif result.kind == "unknown":
        await _enqueue_reconcile(svc, order.id)
    return FlowResult(result.kind, order, view, result.errors)


# -------------------------------------------------------------------------------- admin edit


@dataclass
class AdminEditOutcome:
    kind: Literal["saved", "sent", "rejected", "unknown", "unavailable"]
    view: OrderView
    errors: list[dict[str, Any]] = field(default_factory=list)


async def push_after_admin_edit(
    db: AsyncSession,
    svc: Services,
    order: ShopOrder,
    view: OrderView,
    client: SdClient,
    st: LinkSettings | None,
    actor: Actor,
    changes: dict[str, Any],
    meta: audit.RequestMeta | None,
    before: Any,
    lock: OrderLock | None = None,
) -> AdminEditOutcome:
    """Called with the edit flushed but NOT committed: send to SD, then commit or roll back."""
    status = order_status_for(order, st)
    payload = build_payload(order, view.lines, client, status)
    if lock is not None:
        await lock.renew()
    result = await push_order(svc.sd, payload)
    if result.kind == "sent":
        order.sd_id = result.sd_id or order.sd_id
        order.sd_synced_at = clock.utcnow()
        order.last_error = None
        add_event(db, order, "admin_edit", actor, changes)
        record_audit(db, actor, "order.admin_edit", order, meta, before=before, after=changes)
        await db.commit()
        await enqueue_stock_refresh(svc, order.warehouse_sd_id)
        return AdminEditOutcome("sent", view)
    if result.kind == "unknown":
        # keep the edit locally; reconcile compares SD with it and re-sends the CURRENT content
        mark_unknown(
            order,
            result,
            op=op_doc("admin_edit", status=status, payload_hash=payload_digest(payload)),
        )
        add_event(db, order, "admin_edit", actor, changes)
        add_event(
            db, order, "outcome_unknown", actor, {"message": result.message, "op": "admin_edit"}
        )
        record_audit(db, actor, "order.admin_edit", order, meta, before=before, after=changes)
        await db.commit()
        await _enqueue_reconcile(svc, order.id)
        return AdminEditOutcome("unknown", view)
    await db.rollback()  # SD untouched -> local edit discarded, both sides stay consistent
    return AdminEditOutcome(result.kind, view, result.errors)


# ---------------------------------------------------------------------------- admin status


@dataclass
class StatusOutcome:
    result: PushResult
    order: ShopOrder
    warnings: list[str] = field(default_factory=list)


async def admin_change_status(
    db: AsyncSession,
    svc: Services,
    *,
    public_id: uuid.UUID,
    status: int,
    shipment_date: str | None,
    expected_version: int | None,
    actor: Actor,
    meta: audit.RequestMeta | None,
) -> StatusOutcome:
    """setStatus on a sent order (lock, re-read, re-check, then SD, then write the result)."""
    order = await get_order(db, public_id)
    async with order_lock(svc.redis, order.id) as lock:
        order = await get_order(db, public_id, for_update=True)
        if order.state != "sent":
            state = order.state
            await db.rollback()
            raise problem(409, "invalid_state", state=state)
        check_version(order, expected_version)
        previous = order.sd_status
        warnings = ["cancel_after_shipment_creates_return"] if status == 5 and previous == 2 else []
        await db.commit()  # release the row lock before talking to SD

        await lock.renew()
        result = await push_status(svc.sd, order, status, shipment_date)
        order = await get_order(db, public_id, for_update=True)
        if result.kind == "sent":
            order.sd_status = status
            order.sd_synced_at = clock.utcnow()
            if status == 5:
                order.state = "cancelled"
            await bump_version(db, order)
            add_event(
                db,
                order,
                "cancelled" if status == 5 else "status_changed",
                actor,
                {"from": previous, "to": status, "shipment_date": shipment_date},
            )
            record_audit(
                db,
                actor,
                "order.status",
                order,
                meta,
                before={"sd_status": previous},
                after={"sd_status": status},
            )
            await db.commit()
        elif result.kind == "unknown":
            op = op_doc("status", status=status, shipment_date=shipment_date, previous=previous)
            mark_unknown(order, result, op=op)
            add_event(
                db, order, "outcome_unknown", actor, {"message": result.message, "op": "status"}
            )
            await db.commit()
            await _enqueue_reconcile(svc, order.id)
        else:
            await db.commit()
        return StatusOutcome(result, order, warnings)


# ------------------------------------------------------------------------- discard / delete


async def delete_in_sd(
    sd: SalesDoctorClient, order: ShopOrder, sd_id: str | None = None
) -> PushResult:
    try:
        res = await sd.delete_orders(
            [{"code_1C": order.code_1c, "SD_id": sd_id or order.sd_id or ""}]
        )
    except SDOutcomeUnknownError as exc:
        return PushResult("unknown", message=exc.message)
    except SDValidationError as exc:
        return PushResult("rejected", errors=map_row_errors(exc.items, []), message=exc.message)
    except SDError as exc:
        return PushResult("unavailable", message=exc.message)
    if not res.ok:
        return PushResult(
            "rejected",
            errors=map_row_errors(res.row_errors, []),
            message="Rejected by Sales Doctor",
        )
    return PushResult("sent")


async def discard_order(
    db: AsyncSession,
    svc: Services,
    *,
    public_id: uuid.UUID,
    actor: Actor,
    client_id: int | None,
    hard: bool,
    editable_only: bool,
    expected_version: int | None,
    meta: audit.RequestMeta | None,
) -> tuple[PushResult, ShopOrder]:
    """Delete an order. Anything EVER submitted is first looked up in SD by code_1C: a status-1
    order is cancelled there (stock restored) and setDeletedOrder is sent; only never-submitted
    drafts are purely local (hard-deleted when ``hard``, else marked ``deleted``)."""
    order = await get_order(db, public_id, client_id=client_id)
    async with order_lock(svc.redis, order.id) as lock:
        order = await get_order(db, public_id, for_update=True)
        if order.state in ("deleted", "submitting"):
            state = order.state
            await db.rollback()
            raise problem(409, "invalid_state", state=state)
        if editable_only and order.state not in EDITABLE_STATES:
            state = order.state
            await db.rollback()
            raise problem(409, "order_not_editable", state=state)
        check_version(order, expected_version)
        previous = order.state

        def soft_delete() -> None:
            order.state = "deleted"
            add_event(db, order, "deleted", actor, {"from": previous})
            record_audit(db, actor, "order.delete", order, meta, before={"state": previous})

        if order.submitted_at is None:  # never reached SD
            if hard:
                await db.delete(order)
                await db.commit()
                return PushResult("sent"), order
            soft_delete()
            await bump_version(db, order)
            await db.commit()
            return PushResult("sent"), order

        client, _link, st = await _parts(db, order)
        view = await build_view(db, order, st=st, live=False)
        await db.commit()
        await lock.renew()
        try:
            found = await find_in_sd(svc.sd, order)
        except SDError as exc:
            return PushResult("unavailable", message=exc.message), order
        order = await get_order(db, public_id, for_update=True)
        result = PushResult("sent")
        if found is not None:
            if found.status == 1:
                await lock.renew()
                result = await push_order(svc.sd, build_payload(order, view.lines, client, 5))
                if result.kind == "sent":
                    order.state, order.sd_status = "cancelled", 5
                    order.sd_synced_at = clock.utcnow()
            if result.kind == "sent":
                await lock.renew()
                result = await delete_in_sd(svc.sd, order, found.SD_id)
        if result.kind == "sent":
            soft_delete()
            await bump_version(db, order)
        elif result.kind == "unknown":  # L1: verify later instead of guessing
            mark_unknown(order, result, op=op_doc("delete", previous=previous))
            add_event(
                db, order, "outcome_unknown", actor, {"message": result.message, "op": "delete"}
            )
        await db.commit()
        if result.kind == "unknown":
            await _enqueue_reconcile(svc, order.id)
        elif result.kind == "sent" and order.warehouse_sd_id:
            await enqueue_stock_refresh(svc, order.warehouse_sd_id)
        return result, order


# ------------------------------------------------------------------------------- reconcile


def _reconcile_delay(attempt: int) -> float:
    return float(min(RECONCILE_MAX_DELAY, RECONCILE_BASE_DELAY * (2**attempt)))


ReconcileOutcome = Literal["sent", "failed", "pending", "skipped", "exhausted"]


@dataclass
class Resolved:
    kind: Literal["resolved", "rejected", "retry"]
    final_state: str = "sent"
    sd_status: int | None = None
    sd_id: str | None = None
    via: str = "lookup"
    result: PushResult | None = None
    diverged: bool = False  # SD moved on while our pending edit did not land


def _from_push(result: PushResult, final_state: str, status: int | None, via: str) -> Resolved:
    if result.kind == "sent":
        return Resolved("resolved", final_state, status, result.sd_id, via, result)
    return Resolved("rejected" if result.kind == "rejected" else "retry", result=result, via=via)


async def _resolve(
    svc: Services,
    lock: OrderLock,
    order: ShopOrder,
    view: OrderView,
    client: SdClient,
    st: LinkSettings | None,
    op: dict[str, Any],
) -> Resolved:
    """Operation-aware verification: what did the unknown write actually do in SD?"""
    name = str(op.get("name") or "submit")
    expected = int(op.get("status") or order_status_for(order, st))
    await lock.renew()
    try:
        found = await find_in_sd(svc.sd, order)
    except SDError as exc:
        return Resolved("retry", result=PushResult("unavailable", message=exc.message))

    if name in ("submit", "admin_edit"):
        if found is not None and found.status is not None and found.status != expected:
            # SD moved past what we sent (shipped / cancelled): never overwrite it
            return Resolved(
                "resolved",
                "cancelled" if found.status == 5 else "sent",
                found.status,
                found.SD_id,
                "lookup",
                diverged=not lines_match(found, view.lines),
            )
        if found is not None and lines_match(found, view.lines):
            return Resolved("resolved", "sent", expected, found.SD_id, "lookup")
        await lock.renew()  # missing, or present with OLD content: send the current payload
        payload = build_payload(order, view.lines, client, expected)
        return _from_push(await push_order(svc.sd, payload), "sent", expected, "resend")

    if name == "cancel":
        if found is None or found.status == 5:
            return Resolved("resolved", "cancelled", 5, found.SD_id if found else None)
        if found.status == 1:
            await lock.renew()
            payload = build_payload(order, view.lines, client, 5)
            return _from_push(await push_order(svc.sd, payload), "cancelled", 5, "resend")
        return Resolved("resolved", "sent", found.status, found.SD_id)  # shipped meanwhile

    if name == "status":
        target = int(op["status"])
        final = "cancelled" if target == 5 else "sent"
        if found is None:
            missing = PushResult("rejected", message="Order is missing in Sales Doctor")
            return Resolved("rejected", result=missing)
        if found.status == target:
            return Resolved("resolved", final, target, found.SD_id)
        await lock.renew()
        order.sd_id = order.sd_id or found.SD_id
        res = await push_status(svc.sd, order, target, op.get("shipment_date"))
        return _from_push(res, final, target, "resend")

    # delete
    if found is None:
        return Resolved("resolved", "deleted", order.sd_status)
    if found.status == 1:
        await lock.renew()
        cancel = await push_order(svc.sd, build_payload(order, view.lines, client, 5))
        if cancel.kind != "sent":
            return _from_push(cancel, "deleted", 5, "resend")
    await lock.renew()
    return _from_push(await delete_in_sd(svc.sd, order, found.SD_id), "deleted", 5, "resend")


async def reconcile_order(
    db: AsyncSession,
    svc: Services,
    order_id: int,
    *,
    force: bool = False,
    actor: Actor | None = None,
    meta: audit.RequestMeta | None = None,
) -> ReconcileOutcome:
    """outcome_unknown/submitting -> verify the pending operation in SD, re-send if it did not land.

    Safe to repeat: setOrder with the same code_1C updates the same order. Returns ``pending``
    (and schedules a retry with exponential backoff) while SD stays unreachable. After
    ``RECONCILE_MAX_ATTEMPTS`` the order is PARKED as ``outcome_unknown`` (flag ``exhausted``,
    audit alert, dashboard counter): it is never silently turned into ``failed`` because SD may
    still hold a reservation. ``force`` (admin resend) starts over.
    """
    actor = actor or SYSTEM
    order = await db.get(ShopOrder, order_id)
    if order is None or order.state not in ("outcome_unknown", "submitting"):
        return "skipped"
    age_ref = order.submitted_at or order.created_at
    if not force and clock.utcnow() - age_ref < RECONCILE_MIN_AGE:
        await svc.jobs.enqueue(RECONCILE_ORDER, order_id, defer_by=30)
        return "skipped"
    try:
        lock = await _acquire(svc.redis, order.id)
    except HTTPException:
        return "skipped"
    try:
        order = await get_order(db, order.public_id, for_update=True)
        if order.state not in ("outcome_unknown", "submitting"):
            await db.rollback()
            return "skipped"
        err = order.last_error or {}
        attempts = 0 if force else int(err.get("reconcile_attempts", 0))
        if err.get("exhausted") and not force:
            await db.rollback()
            return "skipped"
        op = pending_op(order) or op_doc("submit")
        client, _link, st = await _parts(db, order)
        view = await build_view(db, order, st=st, live=False)
        await db.commit()  # no row lock held across SD calls

        res = await _resolve(svc, lock, order, view, client, st, op)
        order = await get_order(db, order.public_id, for_update=True)
        name = str(op.get("name") or "submit")
        if res.kind == "resolved":
            now = clock.utcnow()
            order.state = res.final_state
            order.sd_id = res.sd_id or order.sd_id
            if res.sd_status is not None:
                order.sd_status = res.sd_status
            if res.final_state != "deleted":
                order.sent_at = order.sent_at or now
            order.sd_synced_at = now
            order.last_error = None
            await bump_version(db, order)
            add_event(
                db,
                order,
                "reconciled",
                actor,
                {"via": res.via, "op": name, "sd_id": order.sd_id, "diverged": res.diverged},
            )
            if res.diverged:
                audit.record(
                    db,
                    actor_type="system",
                    actor_id=None,
                    action="order.reconcile_diverged",
                    entity_type="shop_order",
                    entity_id=order.id,
                    after={"code": order.code_1c, "op": name, "sd_status": res.sd_status},
                    meta=meta,
                )
            await db.commit()
            if order.warehouse_sd_id:
                await enqueue_stock_refresh(svc, order.warehouse_sd_id)
            return "sent"
        if res.kind == "rejected":
            result = res.result or PushResult("rejected")
            if name in ("submit", "admin_edit"):
                mark_failed(order, result, "sd_rejected")
            else:  # the order itself is fine in SD; only the follow-up operation was refused
                order.state = "sent"
                order.last_error = _error_doc(f"{name}_rejected", result)
            add_event(
                db, order, "sd_error", actor, {"errors": result.errors, "via": res.via, "op": name}
            )
            await db.commit()
            return "failed"

        result = res.result or PushResult("unavailable")
        attempts += 1
        if attempts >= RECONCILE_MAX_ATTEMPTS:
            mark_unknown(order, result, attempts, op, exhausted=True)
            add_event(db, order, "sd_error", actor, {"message": "reconcile attempts exhausted"})
            audit.record(
                db,
                actor_type="system",
                actor_id=None,
                action="order.reconcile_exhausted",
                entity_type="shop_order",
                entity_id=order.id,
                after={
                    "code": order.code_1c,
                    "op": name,
                    "attempts": attempts,
                    "message": result.message,
                },
                meta=meta,
            )
            await db.commit()
            log.error("order_reconcile_exhausted", order=order.code_1c, attempts=attempts)
            return "exhausted"
        mark_unknown(order, result, attempts, op)
        await db.commit()
        await svc.jobs.enqueue(RECONCILE_ORDER, order.id, defer_by=_reconcile_delay(attempts))
        return "pending"
    finally:
        await lock.release()


async def recover_stuck_submitting(
    db: AsyncSession, jobs: JobQueue, redis: Redis | None = None
) -> list[int]:
    """Orders stuck in ``submitting`` > 5 min -> outcome_unknown -> reconcile.

    Runs at worker startup AND from the per-minute sweep; orders whose lock is held (a request
    is still genuinely in flight) are left alone.
    """
    cutoff = clock.utcnow() - STUCK_SUBMITTING_AFTER
    orders = (
        await db.scalars(
            select(ShopOrder)
            .where(ShopOrder.state == "submitting", ShopOrder.submitted_at < cutoff)
            .with_for_update(skip_locked=True)
        )
    ).all()
    ids: list[int] = []
    for order in orders:
        lock = await try_lock(redis, order.id) if redis is not None else None
        if redis is not None and lock is None:
            continue
        try:
            mark_unknown(
                order,
                PushResult("unknown", message="Stuck in submitting; worker restarted"),
                op=op_doc("submit"),
            )
            add_event(db, order, "outcome_unknown", SYSTEM, {"reason": "stuck_submitting"})
            ids.append(order.id)
            await db.flush()
        finally:
            if lock is not None:
                await lock.release()
    await db.commit()
    for order_id in ids:
        await jobs.enqueue(RECONCILE_ORDER, order_id)
    return ids


async def due_unknown_ids(
    db: AsyncSession, limit: int = 200, grace: timedelta = timedelta(0)
) -> list[int]:
    """outcome_unknown orders whose retry time passed ``grace`` ago (sweep for lost jobs).

    Parked (exhausted) orders are skipped: they wait for an operator.
    """
    now = clock.utcnow() - grace
    rows = (
        await db.execute(
            select(ShopOrder.id, ShopOrder.last_error, ShopOrder.updated_at)
            .where(ShopOrder.state == "outcome_unknown")
            .order_by(ShopOrder.id)
            .limit(limit)
        )
    ).all()
    due: list[int] = []
    for order_id, err, updated_at in rows:
        if (err or {}).get("exhausted"):
            continue
        raw = (err or {}).get("next_attempt_at")
        try:
            at = datetime.fromisoformat(raw) if raw else updated_at + RECONCILE_MIN_AGE
        except ValueError:
            at = updated_at + RECONCILE_MIN_AGE
        if at <= now:
            due.append(order_id)
    return due

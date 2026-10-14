"""Shop-facing auth API (prefix /api/shop). Phone-only login through an admin-created link."""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from redis.exceptions import RedisError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    SHOP_COOKIE,
    SHOP_COOKIE_PATH,
    DbDep,
    MetaDep,
    RedisDep,
    SettingsDep,
    ShopDep,
    check_origin,
    clear_session_cookie,
    enforce_rate_limit,
    set_session_cookie,
)
from app.api.schemas import Number
from app.core import clock
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.net import rate_buckets
from app.core.ratelimit import Limit, hit, incr_window
from app.core.security import (
    csrf_token,
    mask_phone,
    phone_display,
    read_client_ref,
    sign_client_ref,
)
from app.domain.phone import phone_key
from app.models import (
    AccessLink,
    SdAgent,
    SdClient,
    SdClientBalance,
    SdPaymentType,
    SdPriceType,
    SdTradeDirection,
    SdWarehouse,
    ShopOrder,
)
from app.services import audit, sessions
from app.services.audit import RequestMeta
from app.services.eligibility import (
    R_NOT_FOUND,
    evaluate_candidates,
    link_accepts_logins,
    parse_settings,
    public_branding,
)
from app.services.orders import num

log = get_logger(__name__)

router = APIRouter(prefix="/api/shop", tags=["shop"], dependencies=[Depends(check_origin)])

SLUG_RE = re.compile(r"^[a-z0-9-]{3,64}$")
GENERIC_DENIED = "Bu raqam bilan do'kon topilmadi yoki ruxsat yo'q"
LINK_INACTIVE = "Bu havola faol emas"


class LoginIn(BaseModel):
    slug: str = Field(max_length=64)
    phone: str = Field(max_length=40)


class SelectIn(BaseModel):
    slug: str = Field(max_length=64)
    client_ref: str = Field(max_length=512)


class ChoiceOut(BaseModel):
    client_ref: str
    name: str | None
    address: str | None


class LoginOut(BaseModel):
    csrf_token: str | None = None
    choose: list[ChoiceOut] | None = None


class LinkInfoOut(BaseModel):
    """``state == "inactive"`` carries only slug + state; branding keys exist when active."""

    slug: str
    state: Literal["active", "inactive"]
    title: str | None = None
    logo_url: str | None = None
    color: str | None = None
    welcome_text: str | None = None


class MeClient(BaseModel):
    id: int
    name: str | None
    address: str | None
    phone: str
    balance: str  # total balance as a decimal string (legacy)


class MeLink(BaseModel):
    slug: str
    title: str | None
    logo_url: str | None
    color: str | None
    welcome_text: str | None


class BalanceByType(BaseModel):
    payment_type_sd_id: str
    name: str | None  # None when the payment type is not (yet) in the mirror
    amount: Number


class MeOption(BaseModel):
    id: str
    name: str | None


class MeOptionSet(BaseModel):
    mode: str
    options: list[MeOption]


class MeOptions(BaseModel):
    """Empty object when the link settings are invalid."""

    warehouses: MeOptionSet | None = None
    price_types: MeOptionSet | None = None
    agents: MeOptionSet | None = None
    trade_directions: MeOptionSet | None = None


class MeLimits(BaseModel):
    """Empty object when the link settings are invalid."""

    min_order_sum: Number | None = None
    max_order_sum: Number | None = None
    allow_over_stock: bool | None = None
    product_filter_mode: str | None = None


class MeOut(BaseModel):
    client: MeClient
    link: MeLink
    options: MeOptions
    limits: MeLimits
    csrf_token: str
    balance_by_type: list[BalanceByType]
    last_price_type_sd_id: str | None


async def _get_link(db: AsyncSession, slug: str) -> AccessLink | None:
    if not SLUG_RE.match(slug):
        return None
    link = await db.scalar(select(AccessLink).where(AccessLink.slug == slug))
    return link if link is not None and link.archived_at is None else None


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


async def _limit_ip(redis: Any, settings: Settings, *, prefix: str, ip: str | None) -> None:
    """Every attempt (successful or not) counts against the caller's address / subnet."""
    primary, subnet = rate_buckets(ip)
    await enforce_rate_limit(
        redis,
        [
            Limit(f"{prefix}:ip:{primary}", settings.RL_SHOP_IP_PER_MIN, 60),
            Limit(f"{prefix}:ip:{primary}", settings.RL_SHOP_IP_PER_HOUR, 3600),
            Limit(f"{prefix}:net:{subnet}", settings.RL_SHOP_SUBNET_PER_MIN, 60),
            Limit(f"{prefix}:net:{subnet}", settings.RL_SHOP_SUBNET_PER_HOUR, 3600),
        ],
    )


async def _deny(
    db: AsyncSession,
    redis: Any,
    settings: Settings,
    link: AccessLink,
    key: str,
    reason: str,
    meta: RequestMeta,
    prefix: str = "shop",
) -> HTTPException:
    """Record a failed attempt and answer 401 (or 429 once a failure limit is exceeded).

    Only FAILED attempts count toward the per-phone, per-slug and per-link limits, so a
    correct login is never throttled by someone else's guessing.
    """
    audit.record(
        db,
        actor_type="system",
        actor_id=None,
        action="shop.login_denied",
        entity_type="access_link",
        entity_id=link.id,
        after={"reason": reason, "phone": mask_phone(key)},
        meta=meta,
    )
    await db.commit()
    log.info("shop_login_denied", reason=reason, phone=mask_phone(key), link=link.slug)
    try:
        link_fails, link_ttl = await incr_window(
            redis, f"linkfail:{link.slug}", settings.RL_SHOP_LINK_FAIL_WINDOW
        )
        retry = await hit(
            redis,
            [
                Limit(f"{prefix}:phone:{_h(key)}", settings.RL_SHOP_PHONE_PER_MIN, 60),
                Limit(f"{prefix}:phone:{_h(key)}", settings.RL_SHOP_PHONE_PER_HOUR, 3600),
                Limit(f"{prefix}:slug:{link.slug}", settings.RL_SHOP_SLUG_PER_MIN, 60),
            ],
        )
    except RedisError as exc:
        log.warning("rate_limit_unavailable", error=type(exc).__name__)
        return HTTPException(503, "Service temporarily unavailable")
    if link_fails > settings.RL_SHOP_LINK_FAIL_THRESHOLD:
        retry = max(retry or 0, link_ttl)
        if link_fails == settings.RL_SHOP_LINK_FAIL_THRESHOLD + 1:  # alert once per window
            log.warning(
                "shop_link_failure_flood",
                link=link.slug,
                failures=link_fails,
                window=settings.RL_SHOP_LINK_FAIL_WINDOW,
            )
            audit.record(
                db,
                actor_type="system",
                actor_id=None,
                action="shop.link_failure_flood",
                entity_type="access_link",
                entity_id=link.id,
                after={"failures": link_fails, "window": settings.RL_SHOP_LINK_FAIL_WINDOW},
                meta=meta,
            )
            await db.commit()
    if retry:
        return HTTPException(
            429, "Too many attempts, try again later", headers={"Retry-After": str(retry)}
        )
    return HTTPException(401, GENERIC_DENIED)


async def _start_session(
    db: AsyncSession,
    settings: Settings,
    response: Response,
    *,
    link: AccessLink,
    client: SdClient,
    key: str,
    meta: RequestMeta,
) -> LoginOut:
    claimed = await db.execute(
        update(AccessLink)
        .where(
            AccessLink.id == link.id,
            (AccessLink.max_logins.is_(None)) | (AccessLink.login_count < AccessLink.max_logins),
        )
        .values(login_count=AccessLink.login_count + 1)
    )
    if claimed.rowcount == 0:  # type: ignore[attr-defined]
        await db.rollback()
        raise HTTPException(403, LINK_INACTIVE)
    token, info = await sessions.create_shop_session(
        db, settings, client_id=client.id, link_id=link.id, phone_key=key, meta=meta
    )
    audit.record(
        db,
        actor_type="shop",
        actor_id=client.id,
        action="shop.login",
        entity_type="access_link",
        entity_id=link.id,
        after={"client_id": client.id, "phone": mask_phone(key), "session_id": info.id},
        meta=meta,
    )
    await db.commit()
    set_session_cookie(
        response,
        settings,
        name=SHOP_COOKIE,
        path=SHOP_COOKIE_PATH,
        token=token,
        max_age=settings.SHOP_SESSION_DAYS * 86400,
    )
    return LoginOut(csrf_token=csrf_token(settings, info.token_hash))


@router.get("/links/{slug}", response_model=LinkInfoOut, response_model_exclude_unset=True)
async def link_info(
    slug: str, db: DbDep, redis: RedisDep, settings: SettingsDep, meta: MetaDep
) -> dict[str, Any]:
    primary, _ = rate_buckets(meta.ip)
    await enforce_rate_limit(
        redis, [Limit(f"shoplink:ip:{primary}", settings.RL_SHOP_LINKINFO_IP_PER_MIN, 60)]
    )
    link = await _get_link(db, slug)
    if link is None:
        raise HTTPException(404, "Not found")
    if not link_accepts_logins(link, clock.utcnow()):
        return {"slug": link.slug, "state": "inactive"}
    b = public_branding(link)
    return {
        "slug": link.slug,
        "state": "active",
        "title": b.title,
        "logo_url": b.logo_url,
        "color": b.color,
        "welcome_text": b.welcome_text,
    }


@router.post("/auth/login", response_model=LoginOut)
async def login(
    body: LoginIn,
    response: Response,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> LoginOut:
    key = phone_key(body.phone)
    if key is None:
        raise HTTPException(422, "Telefon raqami noto'g'ri formatda")
    await _limit_ip(redis, settings, prefix="shop", ip=meta.ip)
    link = await _get_link(db, body.slug)
    if link is None:
        raise HTTPException(404, "Not found")
    if not link_accepts_logins(link, clock.utcnow()):
        raise HTTPException(403, LINK_INACTIVE)

    candidates = await evaluate_candidates(db, key, link)
    eligible = [c for c, reason in candidates if reason is None]
    if not eligible:
        reasons = sorted({r for _, r in candidates if r}) or [R_NOT_FOUND]
        raise await _deny(db, redis, settings, link, key, ",".join(reasons), meta)
    if len(eligible) == 1:
        return await _start_session(
            db, settings, response, link=link, client=eligible[0], key=key, meta=meta
        )
    return LoginOut(
        choose=[
            ChoiceOut(
                client_ref=sign_client_ref(settings, phone_key=key, slug=link.slug, client_id=c.id),
                name=c.name,
                address=c.address,
            )
            for c in eligible
        ]
    )


@router.post("/auth/select", response_model=LoginOut)
async def select_client(
    body: SelectIn,
    response: Response,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> LoginOut:
    ref = read_client_ref(settings, body.client_ref, body.slug)
    if ref is None:
        raise HTTPException(401, "Tanlov muddati tugagan, qaytadan kiring")
    key, client_id = ref
    await _limit_ip(redis, settings, prefix="shopsel", ip=meta.ip)
    # single use: a client_ref can be exchanged for a session only once
    try:
        fresh = await redis.set(
            f"cref:used:{_h(body.client_ref)}", "1", nx=True, ex=settings.CLIENT_REF_TTL + 5
        )
    except RedisError as exc:
        raise HTTPException(503, "Service temporarily unavailable") from exc
    if not fresh:
        raise HTTPException(401, "Tanlov muddati tugagan, qaytadan kiring")
    link = await _get_link(db, body.slug)
    if link is None:
        raise HTTPException(404, "Not found")
    if not link_accepts_logins(link, clock.utcnow()):
        raise HTTPException(403, LINK_INACTIVE)
    verdict = await evaluate_candidates(db, key, link, client_id=client_id)
    if not verdict or verdict[0][1] is not None:
        reason = verdict[0][1] if verdict else R_NOT_FOUND
        raise await _deny(db, redis, settings, link, key, f"select:{reason}", meta, "shopsel")
    return await _start_session(
        db, settings, response, link=link, client=verdict[0][0], key=key, meta=meta
    )


@router.post("/auth/logout", status_code=204)
async def logout(
    ctx: ShopDep,
    response: Response,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> None:
    audit.record(
        db,
        actor_type="shop",
        actor_id=ctx.client.id,
        action="shop.logout",
        entity_type="access_link",
        entity_id=ctx.link.id,
        after={"session_id": ctx.session.id},
        meta=meta,
    )
    await sessions.revoke_shop_sessions(db, redis, session_id=ctx.session.id)
    clear_session_cookie(response, settings, name=SHOP_COOKIE, path=SHOP_COOKIE_PATH)


async def _named(db: AsyncSession, model: Any, ids: list[str], *extra: Any) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows = (
        await db.execute(
            select(model.sd_id, model.name).where(
                model.sd_id.in_(ids), model.active.is_(True), *extra
            )
        )
    ).all()
    by_id: dict[str, str | None] = {str(r[0]): r[1] for r in rows}
    return [{"id": i, "name": by_id[i]} for i in ids if i in by_id]  # keep configured order


async def _balance_by_type(db: AsyncSession, by_currency: Any) -> list[dict[str, Any]]:
    """Non-zero balances per payment type (SD calls the payment type "currency")."""
    entries: list[tuple[str, Decimal]] = []
    for item in by_currency if isinstance(by_currency, list) else []:
        if not isinstance(item, dict) or item.get("currency_id") in (None, ""):
            continue
        try:
            amount = Decimal(str(item.get("amount")))
        except InvalidOperation:
            continue
        if amount.is_finite() and amount != 0:
            entries.append((str(item["currency_id"]), amount))
    if not entries:
        return []
    names = dict(
        (
            await db.execute(
                select(SdPaymentType.sd_id, SdPaymentType.name).where(
                    SdPaymentType.sd_id.in_([e[0] for e in entries])
                )
            )
        ).all()
    )
    return [
        {"payment_type_sd_id": ref, "name": names.get(ref), "amount": num(amount)}
        for ref, amount in entries
    ]


async def _last_price_type(db: AsyncSession, client_id: int) -> str | None:
    """Price type of the client's most recently submitted order (None if never submitted)."""
    return await db.scalar(
        select(ShopOrder.price_type_sd_id)
        .where(
            ShopOrder.client_id == client_id,
            ShopOrder.submitted_at.is_not(None),
            ShopOrder.state != "deleted",
        )
        .order_by(ShopOrder.submitted_at.desc(), ShopOrder.id.desc())
        .limit(1)
    )


@router.get("/me", response_model=MeOut, response_model_exclude_unset=True)
async def me(ctx: ShopDep, db: DbDep) -> dict[str, Any]:
    client, link = ctx.client, ctx.link
    bal_row = (
        await db.execute(
            select(SdClientBalance.balance, SdClientBalance.by_currency).where(
                SdClientBalance.client_id == client.id
            )
        )
    ).one_or_none()
    balance = bal_row[0] if bal_row else None
    st = parse_settings(link)
    options: dict[str, Any] = {}
    limits: dict[str, Any] = {}
    if st is not None:
        agent_ids = st.agents.ids
        if st.agents.mode == "client_assigned":
            assigned = [
                str(a["id"])
                for a in client.agents
                if isinstance(a, dict) and a.get("id") and (not agent_ids or a["id"] in agent_ids)
            ]
            agent_ids = list(dict.fromkeys(assigned))
        options = {
            "warehouses": {
                "mode": st.warehouses.mode,
                "options": await _named(db, SdWarehouse, st.warehouses.ids),
            },
            "price_types": {
                "mode": st.price_types.mode,
                "options": await _named(
                    db, SdPriceType, st.price_types.ids, SdPriceType.kind == "sale"
                ),
            },
            "agents": {"mode": st.agents.mode, "options": await _named(db, SdAgent, agent_ids)},
            "trade_directions": {
                "mode": st.trade_directions.mode,
                "options": await _named(db, SdTradeDirection, st.trade_directions.ids),
            },
        }
        limits = {
            "min_order_sum": st.min_order_sum,
            "max_order_sum": st.max_order_sum,
            "allow_over_stock": st.allow_over_stock,
            "product_filter_mode": st.product_filter.mode,
        }
    b = public_branding(link)
    return {
        "client": {
            "id": client.id,
            "name": client.name,
            "address": client.address,
            "phone": phone_display(ctx.session.phone_key),
            "balance": str(balance if balance is not None else 0),
        },
        "link": {
            "slug": link.slug,
            "title": b.title,
            "logo_url": b.logo_url,
            "color": b.color,
            "welcome_text": b.welcome_text,
        },
        "options": options,
        "limits": limits,
        "csrf_token": ctx.csrf_token,
        "balance_by_type": await _balance_by_type(db, bal_row[1] if bal_row else []),
        "last_price_type_sd_id": await _last_price_type(db, client.id),
    }

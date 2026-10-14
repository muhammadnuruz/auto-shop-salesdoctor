from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.phone import evaluate_phone, phone_keys
from app.models import ClientPhone, PhoneFlag, SdClient
from app.services.app_settings import phone_max_clients
from app.sync.common import (
    SyncOutcome,
    bulk_upsert,
    chunks,
    deactivate_missing,
    dec,
    mirror_row,
    parse_sd_datetime,
    ref_id,
    text_or_none,
    utcnow,
)
from app.sync.context import SyncContext

ADMIN_STATUSES = ("blocked", "allowed")


LAT_MAX = Decimal(90)
LON_MAX = Decimal(180)


def _coord(value: Any, limit: Decimal) -> Decimal | None:
    d = dec(value)
    return d if d is not None and abs(d) <= limit else None


def client_row(
    raw: dict[str, Any], now: datetime, out: SyncOutcome | None = None
) -> dict[str, Any] | None:
    row = mirror_row(raw, now, out)
    if row is None:
        return None
    agents = raw.get("agents")
    lat, lon = _coord(raw.get("lat"), LAT_MAX), _coord(raw.get("lon"), LON_MAX)
    if out is not None and (
        (raw.get("lat") not in (None, "", 0) and lat is None)
        or (raw.get("lon") not in (None, "", 0) and lon is None)
    ):
        out.warn("bad_coordinates")
    row.update(
        firm_name=text_or_none(raw.get("firmName")),
        address=text_or_none(raw.get("address")),
        waymark=text_or_none(raw.get("waymark")),
        tel_raw=text_or_none(raw.get("tel")),
        lat=lat,
        lon=lon,
        category_sd_id=ref_id(raw.get("category")),
        type_sd_id=ref_id(raw.get("type")),
        city_sd_id=ref_id(raw.get("city")),
        expeditor_sd_id=ref_id(raw.get("expeditor")),
        agents=agents if isinstance(agents, list) else [],
        sd_created_at=parse_sd_datetime(raw.get("createdAt")),
        sd_updated_at=parse_sd_datetime(raw.get("updatedAt")),
    )
    return row


async def recompute_phone_flags(session: AsyncSession, max_clients: int) -> dict[str, int]:
    """Recompute phone_flag from client_phone. Admin statuses (blocked/allowed) keep their
    status and reason; only client_count/updated_at are refreshed."""
    now = utcnow()
    counts = dict(
        (
            await session.execute(
                select(ClientPhone.phone_key, func.count(func.distinct(ClientPhone.client_id)))
                .join(SdClient, SdClient.id == ClientPhone.client_id)
                .where(SdClient.active.is_(True))
                .group_by(ClientPhone.phone_key)
            )
        ).all()
    )
    existing = dict((await session.execute(select(PhoneFlag.phone_key, PhoneFlag.status))).all())
    # keys that lost all their clients are reset to count 0
    for key in existing:
        counts.setdefault(key, 0)

    auto_rows: list[dict[str, Any]] = []
    admin_rows: list[dict[str, Any]] = []
    suspicious = 0
    for key, n in counts.items():
        status, reason = evaluate_phone(key, n, max_clients)
        if existing.get(key) in ADMIN_STATUSES:
            admin_rows.append(
                {
                    "phone_key": key,
                    "status": existing[key],
                    "reason": None,
                    "client_count": n,
                    "updated_at": now,
                }
            )
            continue
        suspicious += status == "suspicious"
        auto_rows.append(
            {
                "phone_key": key,
                "status": status,
                "reason": reason,
                "client_count": n,
                "updated_at": now,
            }
        )
    await bulk_upsert(
        session,
        PhoneFlag,
        auto_rows,
        conflict=["phone_key"],
        where=PhoneFlag.status.in_(("ok", "suspicious")),
    )
    await bulk_upsert(
        session,
        PhoneFlag,
        admin_rows,
        conflict=["phone_key"],
        update_cols=["client_count", "updated_at"],
    )
    return {"flags": len(counts), "suspicious": suspicious, "admin_overrides": len(admin_rows)}


async def sync_clients(ctx: SyncContext) -> SyncOutcome:
    out = SyncOutcome()
    now = utcnow()
    rows: list[dict[str, Any]] = []
    # complete-or-raise: iterate_raw raises SDIncompleteFetchError on partial data
    async for raw in ctx.sd.iterate_raw("getClient", "client"):
        try:
            row = client_row(raw, now, out)
        except (ValueError, TypeError, ArithmeticError):
            out.warn("bad_client_row")  # one bad row must never abort the sync
            continue
        if row is None:
            out.warn("client_without_sd_id")
        else:
            rows.append(row)
    if not rows:
        out.warn("empty_response")
        return out

    async with ctx.sessionmaker() as session:
        await bulk_upsert(session, SdClient, rows, conflict=["sd_id"])
        out.detail["deactivated"] = await deactivate_missing(
            session,
            SdClient,
            {r["sd_id"] for r in rows},
            out,
            "client",
            ctx.settings.SYNC_MAX_DEACTIVATE_RATIO,
        )
        out.items = len(rows)

        # SD-sourced phones are diffed; admin-added ones stay.
        ids = dict((await session.execute(select(SdClient.sd_id, SdClient.id))).all())
        wanted: set[tuple[int, str]] = set()
        without_phone = 0
        for r in rows:
            keys = phone_keys(r["tel_raw"])
            if not keys:
                without_phone += 1
            wanted.update((ids[r["sd_id"]], k) for k in keys)
        existing = {
            (cid, key): row_id
            for row_id, cid, key in (
                await session.execute(
                    select(ClientPhone.id, ClientPhone.client_id, ClientPhone.phone_key).where(
                        ClientPhone.source == "sd"
                    )
                )
            ).all()
        }
        stale = [row_id for k, row_id in existing.items() if k not in wanted]
        for batch in chunks(stale):
            await session.execute(delete(ClientPhone).where(ClientPhone.id.in_(batch)))
        new = [
            {"client_id": cid, "phone_key": k, "source": "sd"}
            for cid, k in wanted
            if (cid, k) not in existing
        ]
        await bulk_upsert(
            session, ClientPhone, new, conflict=["client_id", "phone_key"], do_nothing=True
        )
        out.detail["phones"] = len(wanted)
        out.detail["clients_without_valid_phone"] = without_phone
        max_clients = await phone_max_clients(session, ctx.settings)
        out.detail.update(await recompute_phone_flags(session, max_clients))
        await session.commit()
    return out

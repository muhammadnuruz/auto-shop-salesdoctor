from __future__ import annotations

from typing import Any

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import SdClient, SdClientBalance
from app.sync.common import SyncOutcome, bulk_upsert, chunks, dec, utcnow
from app.sync.context import SyncContext


async def sync_balances(ctx: SyncContext) -> SyncOutcome:
    """getBalance only lists clients with transactions: everyone else gets balance 0.

    Zero-fill happens only after a complete, non-empty fetch (iterate_raw raises otherwise).
    """
    out = SyncOutcome()
    raw_rows = [r async for r in ctx.sd.iterate_raw("getBalance", "balance", limit=1000)]
    if not raw_rows:
        out.warn("empty_response")
        return out

    now = utcnow()
    async with ctx.sessionmaker() as session:
        clients = dict((await session.execute(select(SdClient.sd_id, SdClient.id))).all())
        rows: list[dict[str, Any]] = []
        for raw in raw_rows:
            client_id = clients.get(str(raw.get("SD_id") or ""))
            if client_id is None:
                out.warn("unknown_client")
                continue
            by_currency = raw.get("by-currency")
            amount = dec(raw.get("balance"))
            if amount is None and raw.get("balance") not in (None, ""):
                out.warn("bad_balance")
                continue
            rows.append(
                {
                    "client_id": client_id,
                    "balance": amount if amount is not None else 0,
                    "by_currency": by_currency if isinstance(by_currency, list) else [],
                    "synced_at": now,
                }
            )
        await bulk_upsert(session, SdClientBalance, rows, conflict=["client_id"])

        seen = {r["client_id"] for r in rows}
        stale = (
            await session.scalars(
                select(SdClientBalance.client_id).where(
                    (SdClientBalance.balance != 0) | (SdClientBalance.by_currency != [])
                )
            )
        ).all()
        gone = [c for c in stale if c not in seen]
        for batch in chunks(gone):
            await session.execute(
                update(SdClientBalance)
                .where(SdClientBalance.client_id.in_(list(batch)))
                .values(balance=0, by_currency=[], synced_at=now)
                .execution_options(synchronize_session=False)
            )
        # clients that never had a balance row -> explicit zero row
        await session.execute(
            pg_insert(SdClientBalance)
            .from_select(
                ["client_id", "balance", "by_currency", "synced_at"],
                select(SdClient.id, literal_column("0"), literal_column("'[]'::jsonb"), func.now()),
            )
            .on_conflict_do_nothing(index_elements=["client_id"])
        )
        out.items = len(rows)
        out.detail["zeroed"] = len(gone)
        await session.commit()
    return out

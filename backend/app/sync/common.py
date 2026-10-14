"""Shared helpers for sync jobs."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.salesdoctor.schemas import _to_bool

SD_TZ = ZoneInfo("Asia/Tashkent")  # SD timestamps are naive local time
BATCH = 1000
MAX_BIND_PARAMS = 30000
# Always allow deactivating a handful of rows (small reference tables).
DEACTIVATE_ABS_ALLOWANCE = 3
DEFAULT_MAX_ABS = Decimal(10) ** 13  # safe for numeric(18,4)
INT32_MAX = 2**31 - 1


@dataclass
class SyncOutcome:
    """What a sync function reports back to the runner."""

    items: int = 0
    warnings: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def warn(self, key: str, count: int = 1) -> None:
        self.warnings += count
        self.detail[key] = self.detail.get(key, 0) + count


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_bool(value: Any) -> bool:
    return bool(_to_bool(value))


def dec(value: Any, max_abs: Decimal = DEFAULT_MAX_ABS) -> Decimal | None:
    """Decimal from SD value; None for empty, non-numeric, non-finite or out-of-range input."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    if not d.is_finite() or abs(d) >= max_abs:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return d


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def ref_id(value: Any) -> str | None:
    """SD_id of an EntityID dict (``{"SD_id": ..}``), None when empty."""
    if isinstance(value, Mapping):
        return text_or_none(value.get("SD_id"))
    return None


def parse_sd_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=SD_TZ)
    except ValueError:
        return None


def mirror_row(
    raw: Mapping[str, Any], now: datetime, out: SyncOutcome | None = None
) -> dict[str, Any] | None:
    """Common columns of an SDMirrorMixin row; None if the SD row has no SD_id.

    If SD omits ``active`` the key is left out so an upsert keeps the existing value (new rows
    get the model default) and a warning is counted.
    """
    sd_id = text_or_none(raw.get("SD_id"))
    if sd_id is None:
        return None
    row: dict[str, Any] = {
        "sd_id": sd_id,
        "cs_id": text_or_none(raw.get("CS_id")),
        "code_1c": text_or_none(raw.get("code_1C")),
        "name": raw.get("name"),
        "raw": dict(raw),
        "synced_at": now,
    }
    if raw.get("active") is None:
        if out is not None:
            out.warn("active_missing")
    else:
        row["active"] = to_bool(raw.get("active"))
    return row


def chunks[T](items: Sequence[T], size: int = BATCH) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def bulk_upsert(
    session: AsyncSession,
    model: Any,
    rows: Sequence[dict[str, Any]],
    *,
    conflict: Sequence[str],
    update_cols: Sequence[str] | None = None,
    where: ColumnElement[bool] | None = None,
    do_nothing: bool = False,
    skip_unchanged: bool = True,
) -> int:
    """INSERT ... ON CONFLICT DO UPDATE in batches.

    * rows are de-duplicated on ``conflict`` (last wins);
    * rows with different column sets are upserted in separate groups (so a missing ``active``
      never overwrites the stored value);
    * unchanged rows are not rewritten (``IS DISTINCT FROM`` on all updated columns except
      synced_at/updated_at), avoiding dead tuples on every sync.
    """
    if not rows:
        return 0
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        unique[tuple(r[c] for c in conflict)] = r
    groups: dict[frozenset[str], list[dict[str, Any]]] = {}
    for r in unique.values():
        groups.setdefault(frozenset(r), []).append(r)

    for items in groups.values():
        cols = list(items[0].keys())
        ucols = [c for c in cols if c not in conflict]
        if update_cols is not None:
            ucols = [c for c in ucols if c in update_cols]
        dcols = [c for c in ucols if c not in ("synced_at", "updated_at")]
        size = max(1, min(BATCH, MAX_BIND_PARAMS // len(cols)))
        for batch in chunks(items, size):
            stmt = pg_insert(model).values(list(batch))
            if do_nothing:
                stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict))
            else:
                cond = where
                if skip_unchanged and dcols:
                    distinct = tuple_(*[getattr(model, c) for c in dcols]).is_distinct_from(
                        tuple_(*[stmt.excluded[c] for c in dcols])
                    )
                    cond = distinct if cond is None else and_(cond, distinct)
                stmt = stmt.on_conflict_do_update(
                    index_elements=list(conflict),
                    set_={c: stmt.excluded[c] for c in ucols},
                    where=cond,
                )
            await session.execute(stmt)
    return len(unique)


async def deactivate_missing(
    session: AsyncSession,
    model: Any,
    seen_sd_ids: set[str],
    out: SyncOutcome,
    label: str,
    max_ratio: float,
) -> int:
    """Deactivate active rows absent from a VERIFIED-COMPLETE fetch. Never deletes.

    Safety cap: if more than ``max_ratio`` of the active rows (and more than a tiny absolute
    allowance) would be deactivated, nothing is deactivated and a warning is recorded.
    """
    active = set((await session.scalars(select(model.sd_id).where(model.active.is_(True)))).all())
    missing = sorted(active - seen_sd_ids)
    if not missing:
        return 0
    if len(missing) > DEACTIVATE_ABS_ALLOWANCE and len(missing) > max_ratio * len(active):
        out.warn(f"{label}_deactivation_skipped")
        out.detail[f"{label}_deactivation_skipped_info"] = {
            "would_deactivate": len(missing),
            "active": len(active),
            "max_ratio": max_ratio,
        }
        return 0
    for batch in chunks(missing):
        await session.execute(
            update(model)
            .where(model.sd_id.in_(list(batch)))
            .values(active=False)
            .execution_options(synchronize_session=False)
        )
    return len(missing)

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, Identity, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SYNC_STATUSES = ("running", "ok", "error")


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    entity: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Enum(*SYNC_STATUSES, name="sync_status"), nullable=False)
    items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_sync_run_entity_started_at", "entity", text("started_at DESC")),
        Index("ix_sync_run_started_at", text("started_at DESC")),
        Index("ix_sync_run_running", "started_at", postgresql_where=text("status = 'running'")),
        # notifier: finished runs after a (finished_at, id) cursor; previous run per entity
        Index("ix_sync_run_finished_at_id", "finished_at", "id"),
        Index("ix_sync_run_entity_finished_at", "entity", "finished_at"),
    )

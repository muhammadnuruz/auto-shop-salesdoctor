from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column


class SDMirrorMixin:
    """Columns shared by every table mirrored from Sales Doctor.

    Rows are never hard-deleted: entities missing from a full sync get ``active = false``.
    """

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    sd_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    cs_id: Mapped[str | None] = mapped_column(Text)
    code_1c: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

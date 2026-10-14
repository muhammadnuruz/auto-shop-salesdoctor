"""Shop orders (Phase 4): orders, lines, timeline events, per-product admin settings.

``order`` FKs to ``sd_client`` / ``sd_product`` / ``access_link`` are ON DELETE RESTRICT.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.auth import ACTOR_TYPES

ORDER_CREATORS = ("shop", "admin")
ORDER_STATES = (
    "draft",
    "submitting",
    "sent",
    "failed",
    "outcome_unknown",
    "cancelled",
    "deleted",
)
ORDER_EVENTS = (
    "created",
    "updated",
    "submitted",
    "sd_ok",
    "sd_error",
    "outcome_unknown",
    "reconciled",
    "cancelled",
    "deleted",
    "admin_edit",
    "status_changed",
)

_NOW = func.now()


class ShopOrder(Base):
    __tablename__ = "shop_order"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    code_1c: Mapped[str] = mapped_column(Text, nullable=False)  # "WEB-" + zero-padded id
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_client.id", ondelete="RESTRICT"), nullable=False
    )
    link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("access_link.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_type: Mapped[str] = mapped_column(
        Enum(*ORDER_CREATORS, name="order_creator"), nullable=False, default="shop"
    )
    created_by_admin_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("admin_user.id", ondelete="RESTRICT")
    )
    agent_sd_id: Mapped[str | None] = mapped_column(Text)
    warehouse_sd_id: Mapped[str | None] = mapped_column(Text)
    price_type_sd_id: Mapped[str | None] = mapped_column(Text)
    trade_sd_id: Mapped[str | None] = mapped_column(Text)
    shipment_date: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    state: Mapped[str] = mapped_column(
        Enum(*ORDER_STATES, name="order_state"), nullable=False, default="draft"
    )
    sd_status: Mapped[int | None] = mapped_column(SmallInteger)  # 1..5 as reported by SD
    sd_id: Mapped[str | None] = mapped_column(Text)
    total_sum: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=0, server_default="0"
    )
    total_qty: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), nullable=False, default=0, server_default="0"
    )
    lines_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sd_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=_NOW
    )

    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012  (read updated_at back)

    __table_args__ = (
        UniqueConstraint("public_id", name="uq_shop_order_public_id"),
        UniqueConstraint("code_1c", name="uq_shop_order_code_1c"),
        CheckConstraint("sd_status IS NULL OR sd_status BETWEEN 1 AND 5", name="sd_status_range"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("lines_count >= 0", name="lines_count_nonneg"),
        Index("ix_shop_order_client_id_created_at", "client_id", text("created_at DESC")),
        Index("ix_shop_order_state", "state"),
        Index("ix_shop_order_link_id_created_at", "link_id", text("created_at DESC")),
        Index("ix_shop_order_created_at", text("created_at DESC")),
        Index("ix_shop_order_sd_id", "sd_id"),
        Index("ix_shop_order_created_by_admin_id", "created_by_admin_id"),
    )


class ShopOrderLine(Base):
    __tablename__ = "shop_order_line"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shop_order.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_product.id", ondelete="RESTRICT"), nullable=False
    )
    blocks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    pieces: Mapped[Decimal] = mapped_column(
        Numeric(18, 3), nullable=False, default=0, server_default="0"
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), nullable=False, default=0, server_default="0"
    )
    line_sum: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=0, server_default="0"
    )
    comment: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_shop_order_line_order_id_product_id"),
        CheckConstraint("blocks >= 0", name="blocks_nonneg"),
        CheckConstraint("pieces >= 0", name="pieces_nonneg"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        Index("ix_shop_order_line_product_id", "product_id"),
    )


class ShopOrderEvent(Base):
    """Order timeline (shown in the admin detail view)."""

    __tablename__ = "shop_order_event"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shop_order.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    actor_type: Mapped[str] = mapped_column(
        Enum(*ACTOR_TYPES, name="order_event_actor"), nullable=False
    )
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    event: Mapped[str] = mapped_column(Enum(*ORDER_EVENTS, name="order_event_type"), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_shop_order_event_order_id_at", "order_id", "at"),)


class ProductSetting(Base):
    """Admin-side overrides of a mirrored product (hide from the shop catalog, note)."""

    __tablename__ = "product_setting"

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_product.id", ondelete="CASCADE"), primary_key=True
    )
    hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=_NOW
    )

    __table_args__ = (Index("ix_product_setting_updated_by", "updated_by"),)

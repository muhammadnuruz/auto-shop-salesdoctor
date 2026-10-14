"""Local mirror of Sales Doctor data."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import SDMirrorMixin

BARCODE_SOURCES = ("sd", "admin")
PHONE_SOURCES = ("sd", "admin")
PHONE_STATUSES = ("ok", "suspicious", "blocked", "allowed")
PHONE_KEY_CHECK = "phone_key ~ '^[0-9]{9}$'"


class SdWarehouse(SDMirrorMixin, Base):
    __tablename__ = "sd_warehouse"


class SdPriceType(SDMirrorMixin, Base):
    __tablename__ = "sd_price_type"

    kind: Mapped[str | None] = mapped_column(Text)  # SD "type": sale / purchase / ...
    payment_type_sd_id: Mapped[str | None] = mapped_column(Text)


class SdTradeDirection(SDMirrorMixin, Base):
    __tablename__ = "sd_trade_direction"


class SdPaymentType(SDMirrorMixin, Base):
    __tablename__ = "sd_payment_type"


class SdAgent(SDMirrorMixin, Base):
    __tablename__ = "sd_agent"


class SdUnit(SDMirrorMixin, Base):
    __tablename__ = "sd_unit"


class SdProductCategory(SDMirrorMixin, Base):
    __tablename__ = "sd_product_category"


class SdBrand(SDMirrorMixin, Base):
    __tablename__ = "sd_brand"


class SdProduct(SDMirrorMixin, Base):
    __tablename__ = "sd_product"

    sort: Mapped[int | None] = mapped_column(Integer)
    pack_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    image_id: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    thumb_url: Mapped[str | None] = mapped_column(Text)
    category_sd_id: Mapped[str | None] = mapped_column(Text)
    sub_category_sd_id: Mapped[str | None] = mapped_column(Text)
    unit_sd_id: Mapped[str | None] = mapped_column(Text)
    brand_sd_id: Mapped[str | None] = mapped_column(Text)
    trade_sd_id: Mapped[str | None] = mapped_column(Text)
    barcode: Mapped[str | None] = mapped_column(Text)  # SD original value

    __table_args__ = (
        Index(
            "ix_sd_product_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("ix_sd_product_category_sd_id", "category_sd_id"),
        Index("ix_sd_product_trade_sd_id", "trade_sd_id"),
    )


class ProductBarcode(Base):
    """Barcodes per product. NOT unique across products (SD has duplicates)."""

    __tablename__ = "product_barcode"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_product.id", ondelete="CASCADE"), nullable=False
    )
    barcode: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        Enum(*BARCODE_SOURCES, name="barcode_source"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("barcode", "product_id", name="uq_product_barcode_barcode_product"),
        Index("ix_product_barcode_product_id", "product_id"),
    )


class SdPrice(Base):
    __tablename__ = "sd_price"

    price_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_price_type.id", ondelete="CASCADE")
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_product.id", ondelete="CASCADE")
    )
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("price_type_id", "product_id", name="pk_sd_price"),
        Index("ix_sd_price_product_id", "product_id"),
    )


class SdStock(Base):
    __tablename__ = "sd_stock"

    warehouse_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_warehouse.id", ondelete="CASCADE")
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_product.id", ondelete="CASCADE")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("warehouse_id", "product_id", name="pk_sd_stock"),
        Index("ix_sd_stock_product_id", "product_id"),
    )


class SdClient(SDMirrorMixin, Base):
    __tablename__ = "sd_client"

    firm_name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    waymark: Mapped[str | None] = mapped_column(Text)
    tel_raw: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    lon: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    category_sd_id: Mapped[str | None] = mapped_column(Text)
    type_sd_id: Mapped[str | None] = mapped_column(Text)
    city_sd_id: Mapped[str | None] = mapped_column(Text)
    expeditor_sd_id: Mapped[str | None] = mapped_column(Text)
    agents: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    sd_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sd_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_sd_client_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class ClientPhone(Base):
    __tablename__ = "client_phone"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_client.id", ondelete="CASCADE"), nullable=False
    )
    phone_key: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Enum(*PHONE_SOURCES, name="phone_source"), nullable=False)

    __table_args__ = (
        UniqueConstraint("client_id", "phone_key", name="uq_client_phone_client_phone"),
        # Phase 3 note: login lookups must join sd_client.active.
        Index("ix_client_phone_phone_key_client_id", "phone_key", "client_id"),
        CheckConstraint(PHONE_KEY_CHECK, name="phone_key_format"),
    )


class PhoneFlag(Base):
    __tablename__ = "phone_flag"
    __table_args__ = (CheckConstraint(PHONE_KEY_CHECK, name="phone_key_format"),)

    phone_key: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(
        Enum(*PHONE_STATUSES, name="phone_flag_status"), nullable=False, default="ok"
    )
    reason: Mapped[str | None] = mapped_column(Text)
    client_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SdClientBalance(Base):
    __tablename__ = "sd_client_balance"

    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_client.id", ondelete="CASCADE"), primary_key=True
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    by_currency: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

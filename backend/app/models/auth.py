"""Auth / admin domain: admin users, access links, sessions, client settings, audit log.

Note for Phase 4: ``order`` FKs to ``sd_client`` / ``sd_product`` / ``access_link`` must be
``ON DELETE RESTRICT`` and indexed.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.sd import PHONE_KEY_CHECK

ADMIN_ROLES = ("superadmin", "admin", "operator", "viewer")
CLIENT_SCOPES = ("all", "selected", "territory", "category")
ACTOR_TYPES = ("admin", "shop", "system")
SLUG_CHECK = "slug ~ '^[a-z0-9-]{3,64}$'"

_NOW = func.now()


def _ts(**kw: Any) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=_NOW, **kw)


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False)  # stored lower-case
    full_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Enum(*ADMIN_ROLES, name="admin_role"), nullable=False)
    extra_permissions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    totp_secret: Mapped[str | None] = mapped_column(Text)  # Fernet-encrypted
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=_NOW
    )

    __table_args__ = (
        UniqueConstraint("username", name="uq_admin_user_username"),
        CheckConstraint("username = lower(username)", name="username_lower"),
    )


class AccessLink(Base):
    __tablename__ = "access_link"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_logins: Mapped[int | None] = mapped_column(Integer)
    login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_scope: Mapped[str] = mapped_column(
        Enum(*CLIENT_SCOPES, name="link_client_scope"), nullable=False, default="all"
    )
    scope_values: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("admin_user.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=_NOW
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("slug", name="uq_access_link_slug"),
        CheckConstraint(SLUG_CHECK, name="slug_format"),
        CheckConstraint("max_logins IS NULL OR max_logins >= 1", name="max_logins_positive"),
        CheckConstraint("login_count >= 0", name="login_count_nonneg"),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at", name="period"
        ),
        Index("ix_access_link_created_by", "created_by"),
        Index("ix_access_link_archived_at", "archived_at"),
    )


class LinkClient(Base):
    __tablename__ = "link_client"

    link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("access_link.id", ondelete="CASCADE"), primary_key=True
    )
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_client.id", ondelete="RESTRICT"), primary_key=True
    )

    __table_args__ = (Index("ix_link_client_client_id", "client_id"),)


class ClientSetting(Base):
    __tablename__ = "client_setting"

    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_client.id", ondelete="RESTRICT"), primary_key=True
    )
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("admin_user.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW, onupdate=_NOW
    )

    __table_args__ = (Index("ix_client_setting_updated_by", "updated_by"),)


class ShopSession(Base):
    """Server-side shop session. Only the SHA-256 of the opaque cookie token is stored."""

    __tablename__ = "shop_session"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sd_client.id", ondelete="RESTRICT"), nullable=False
    )
    link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("access_link.id", ondelete="RESTRICT"), nullable=False
    )
    phone_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_shop_session_token_hash"),
        CheckConstraint(PHONE_KEY_CHECK, name="phone_key_format"),
        Index("ix_shop_session_client_id", "client_id"),
        Index("ix_shop_session_link_id", "link_id"),
        Index("ix_shop_session_phone_key", "phone_key"),
        Index("ix_shop_session_expires_at", "expires_at"),
    )


class AdminSession(Base):
    __tablename__ = "admin_session"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    admin_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("admin_user.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_admin_session_token_hash"),
        Index("ix_admin_session_admin_user_id", "admin_user_id"),
        Index("ix_admin_session_expires_at", "expires_at"),
    )


class AuditLog(Base):
    """Append-only audit trail. ``before``/``after`` never contain secrets (see services.audit)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    at: Mapped[datetime] = _ts()
    actor_type: Mapped[str] = mapped_column(Enum(*ACTOR_TYPES, name="audit_actor"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_audit_log_at", text("at DESC")),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_actor", "actor_type", "actor_id"),
    )

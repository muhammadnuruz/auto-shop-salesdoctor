"""Audit trail writer. Never stores secrets: sensitive keys are dropped before persisting."""

from dataclasses import dataclass
from typing import Any

from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

_SENSITIVE_PARTS = ("hash", "secret", "token", "password", "totp", "cookie")


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


def sanitize(value: Any) -> Any:
    """JSON-safe deep copy without sensitive keys."""
    value = to_jsonable_python(value, fallback=str)
    if isinstance(value, dict):
        return {
            k: sanitize(v)
            for k, v in value.items()
            if not any(p in str(k).lower() for p in _SENSITIVE_PARTS)
        }
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def record(
    db: AsyncSession,
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    before: Any = None,
    after: Any = None,
    meta: RequestMeta | None = None,
) -> None:
    """Add an audit row to the session (committed together with the caller's transaction)."""
    meta = meta or RequestMeta()
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=None if entity_id is None else str(entity_id),
            before=None if before is None else sanitize(before),
            after=None if after is None else sanitize(after),
            ip=meta.ip,
            user_agent=(meta.user_agent or "")[:512] or None,
            request_id=meta.request_id,
        )
    )

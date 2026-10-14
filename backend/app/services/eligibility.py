"""Who may log in through a link: client/phone/scope rules shared by login and revalidation."""

from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.link_settings import Branding, LinkSettings
from app.models import AccessLink, ClientPhone, ClientSetting, LinkClient, PhoneFlag, SdClient

# Reasons are internal only (audit); the API answers with one generic message.
R_CLIENT_INACTIVE = "client_inactive"
R_CLIENT_BLOCKED = "client_blocked"
R_PHONE_BLOCKED = "phone_blocked"
R_PHONE_SUSPICIOUS = "phone_suspicious"
R_OUT_OF_SCOPE = "out_of_scope"
R_NOT_FOUND = "not_found"


def link_usable(link: AccessLink, now: datetime) -> bool:
    """Link may serve existing sessions: not archived/stopped and inside its validity period."""
    if link.archived_at is not None or not link.active:
        return False
    if link.starts_at is not None and link.starts_at > now:
        return False
    return not (link.ends_at is not None and link.ends_at <= now)


def link_accepts_logins(link: AccessLink, now: datetime) -> bool:
    return link_usable(link, now) and (
        link.max_logins is None or link.login_count < link.max_logins
    )


def parse_settings(link: AccessLink) -> LinkSettings | None:
    try:
        return LinkSettings.model_validate(link.settings)
    except ValidationError:
        return None


def public_branding(link: AccessLink) -> Branding:
    try:
        raw = link.settings.get("branding") if isinstance(link.settings, dict) else None
        return Branding.model_validate(raw or {})
    except ValidationError:
        return Branding()


async def evaluate_candidates(
    db: AsyncSession, phone_key: str, link: AccessLink, *, client_id: int | None = None
) -> list[tuple[SdClient, str | None]]:
    """Every client owning ``phone_key`` with the rejection reason (None = eligible)."""
    stmt = (
        select(SdClient, PhoneFlag.status, ClientSetting.blocked)
        .join(ClientPhone, ClientPhone.client_id == SdClient.id)
        .outerjoin(PhoneFlag, PhoneFlag.phone_key == ClientPhone.phone_key)
        .outerjoin(ClientSetting, ClientSetting.client_id == SdClient.id)
        .where(ClientPhone.phone_key == phone_key)
        .order_by(SdClient.name, SdClient.id)
    )
    if client_id is not None:
        stmt = stmt.where(SdClient.id == client_id)
    rows = (await db.execute(stmt)).all()
    selected: set[int] = set()
    if link.client_scope == "selected" and rows:
        selected = set(
            (
                await db.scalars(
                    select(LinkClient.client_id).where(
                        LinkClient.link_id == link.id,
                        LinkClient.client_id.in_([r[0].id for r in rows]),
                    )
                )
            ).all()
        )
    out: list[tuple[SdClient, str | None]] = []
    for client, flag, blocked in rows:
        reason: str | None = None
        if not client.active:
            reason = R_CLIENT_INACTIVE
        elif blocked:
            reason = R_CLIENT_BLOCKED
        elif flag == "blocked":
            reason = R_PHONE_BLOCKED
        elif flag == "suspicious":
            reason = R_PHONE_SUSPICIOUS
        elif not _in_scope(link, client, selected):
            reason = R_OUT_OF_SCOPE
        out.append((client, reason))
    return out


def _in_scope(link: AccessLink, client: SdClient, selected: set[int]) -> bool:
    scope = link.client_scope
    if scope == "all":
        return True
    if scope == "selected":
        return client.id in selected
    if scope == "territory":
        return client.city_sd_id in link.scope_values
    if scope == "category":
        return client.category_sd_id in link.scope_values
    return False

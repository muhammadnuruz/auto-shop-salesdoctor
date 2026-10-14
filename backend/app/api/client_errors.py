"""Browser error reports (unauthenticated, rate limited): logged and forwarded to Sentry.

The SPA posts uncaught errors here instead of talking to a third-party endpoint, so the CSP
stays ``connect-src 'self'`` and no browser SDK is needed.
"""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.api.deps import RedisDep, SettingsDep, check_origin, enforce_rate_limit, get_meta
from app.core.logging import get_logger
from app.core.net import rate_buckets
from app.core.observability import capture_client_error
from app.core.ratelimit import Limit
from app.services.audit import RequestMeta

router = APIRouter(prefix="/api", tags=["client-errors"], dependencies=[Depends(check_origin)])
log = get_logger(__name__)


class ClientError(BaseModel):
    message: str = Field(max_length=500)
    stack: str | None = Field(None, max_length=4000)
    url: str | None = Field(None, max_length=500)
    source: str | None = Field(None, max_length=40)  # boundary | route | window | promise
    release: str | None = Field(None, max_length=64)


@router.post("/client-errors", status_code=204)
async def report_client_error(
    body: ClientError,
    redis: RedisDep,
    settings: SettingsDep,
    meta: Annotated[RequestMeta, Depends(get_meta)],
) -> Response:
    primary, subnet = rate_buckets(meta.ip)  # IPv6: per /64 and /48, not per address
    per_min = settings.RL_CLIENT_ERRORS_PER_MIN
    await enforce_rate_limit(
        redis,
        [
            Limit(f"clienterr:{primary}", per_min, 60),
            Limit(f"clienterr:net:{subnet}", per_min * 5, 60),
            # global cap: an unauthenticated endpoint must not flood logs / the Sentry quota
            Limit("clienterr:all", settings.RL_CLIENT_ERRORS_GLOBAL_PER_MIN, 60),
        ],
    )
    # keep only the path: query strings / fragments may carry tokens or slugs + phones
    path = urlsplit(body.url).path if body.url else None
    payload = {
        "message": body.message,
        "stack": body.stack,
        "path": path,
        "source": body.source,
        "release": body.release,
        "user_agent": (meta.user_agent or "")[:300],
        "request_id": meta.request_id,
    }
    log.warning("client_error", **payload)
    capture_client_error(payload)
    return Response(status_code=204)

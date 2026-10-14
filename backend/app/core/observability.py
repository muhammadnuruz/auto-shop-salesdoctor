"""Optional error tracking (Sentry). Off unless SENTRY_DSN is set.

Privacy: no PII is sent (``send_default_pii=False``), request bodies, cookies, auth/CSRF
headers and query strings are dropped, and every remaining value goes through the same
secret redaction as the logs. Phone numbers never leave the server unmasked.
"""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger, redact

log = get_logger(__name__)

_DROP_HEADERS = {"cookie", "authorization", "x-csrf-token", "set-cookie", "x-forwarded-for"}


def scrub_event(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """``before_send``: remove anything that could carry secrets or personal data."""
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("cookies", None)
        request.pop("data", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: v for k, v in headers.items() if k.lower() not in _DROP_HEADERS
            }
    event.pop("user", None)
    scrubbed: dict[str, Any] = redact(event)
    return scrubbed


def init_sentry(settings: Settings, component: str) -> bool:
    """Initialise Sentry for ``component`` ("api" | "worker"); returns whether it is on."""
    dsn = settings.SENTRY_DSN.get_secret_value() if settings.SENTRY_DSN else ""
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.ENV,
        release=settings.APP_RELEASE or None,
        send_default_pii=False,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        before_send=scrub_event,  # type: ignore[arg-type]
        max_request_body_size="never",
    )
    sentry_sdk.set_tag("component", component)
    log.info("sentry_enabled", component=component)
    return True


def capture_client_error(payload: dict[str, Any]) -> None:
    """Forward a browser error report to Sentry when it is enabled (no-op otherwise)."""
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - dependency is installed in the image
        return
    if not sentry_sdk.get_client().is_active():
        return
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("component", "frontend")
        scope.set_context("client", redact(payload))
        sentry_sdk.capture_message(f"frontend: {payload.get('message', '')[:200]}", level="error")

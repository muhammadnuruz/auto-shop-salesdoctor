"""Single source of "now" so tests can freeze/advance time (always call ``clock.utcnow()``)."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)

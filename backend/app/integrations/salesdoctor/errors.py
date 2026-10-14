"""Sales Doctor error model.

Messages never include tokens or passwords; only method names, codes and SD-provided text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SDRowError:
    """One failed row of a write call (set*)."""

    entity: str
    index: int | None
    ids: dict[str, Any]
    messages: list[str]


class SDError(Exception):
    """Base class for every Sales Doctor failure."""

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.method = method
        self.code = code

    def __str__(self) -> str:
        parts = [f"method={self.method}" if self.method else "", f"code={self.code}"]
        prefix = " ".join(p for p in parts if p and p != "code=None")
        return f"{self.message} ({prefix})" if prefix else self.message


class SDTransportError(SDError):
    """Network failure, timeout, HTTP 5xx/403 or unparsable body."""

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        http_status: int | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message, method=method, code=http_status)
        self.http_status = http_status
        self.retryable = retryable


class SDOutcomeUnknownError(SDError):
    """A write (set*) may or may not have been applied (read timeout, 5xx, dropped connection).

    Callers must reconcile (e.g. getOrder by code_1C) before deciding to resend.
    """


class SDIncompleteFetchError(SDError):
    """A paginated read ended before all rows were collected (or the server ignored paging).

    Callers must not treat the partial data as complete (e.g. never deactivate "missing" rows).
    """


class SDAuthError(SDError):
    """Code 401: token invalid. The client re-logins once and retries."""


class SDMethodError(SDError):
    """Code 402: unknown method."""


class SDTimeWindowError(SDError):
    """Method only allowed in a time window (error returned as a plain string)."""


class SDValidationError(SDError):
    """Code 400: write validation failure with per-row details."""

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        items: list[SDRowError] | None = None,
    ) -> None:
        super().__init__(message, method=method, code=400)
        self.items: list[SDRowError] = items or []

    def __str__(self) -> str:
        base = super().__str__()
        if not self.items:
            return base
        detail = "; ".join(
            f"{i.entity}[{i.index}]: {', '.join(i.messages)}" for i in self.items[:10]
        )
        return f"{base}: {detail}"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


_ID_KEYS = ("CS_id", "SD_id", "code_1C")


def parse_row_errors(data: Any) -> list[SDRowError]:
    """Parse ``error.data`` = ``{entity: [{SD_id, code_1C, index, errors: [...]}, ...]}``."""
    rows: list[SDRowError] = []
    if not isinstance(data, Mapping):
        return rows
    for entity, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            raw_msgs = item.get("errors")
            if isinstance(raw_msgs, list):
                messages = [str(m) for m in raw_msgs]
            elif raw_msgs:
                messages = [str(raw_msgs)]
            else:
                messages = []
            index = _as_int(item.get("index"))
            rows.append(
                SDRowError(
                    entity=str(entity),
                    index=index,
                    ids={k: item.get(k) for k in _ID_KEYS if k in item},
                    messages=messages,
                )
            )
    return rows


def raise_for_payload(method: str, payload: Mapping[str, Any]) -> None:
    """Raise the matching SDError if the decoded JSON body reports a failure."""
    if payload.get("status") is True:
        return

    error = payload.get("error")
    if isinstance(error, str):
        if "allowed time" in error.lower():
            raise SDTimeWindowError(error, method=method)
        raise SDError(error, method=method)

    if isinstance(error, Mapping):
        code = _as_int(error.get("code"))
        message = str(error.get("message") or "SD error")
        if code == 401:
            raise SDAuthError(message, method=method, code=401)
        if code == 402:
            raise SDMethodError(message, method=method, code=402)
        if code == 400:
            raise SDValidationError(
                message, method=method, items=parse_row_errors(error.get("data"))
            )
        raise SDError(message, method=method, code=code)

    raise SDError("SD returned status=false without error", method=method)

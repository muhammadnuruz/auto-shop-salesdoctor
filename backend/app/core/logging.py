"""Structured JSON logging with secret redaction."""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "***"
_SENSITIVE_KEYS = {
    "token",
    "password",
    "passwd",
    "secret",
    "authorization",
    "auth",
    "api_key",
    "apikey",
    "sd_password",
    "cookie",
    "set-cookie",
}
_TOKEN_IN_TEXT = re.compile(
    r"""(?ix)
    (["']?(?:token|password|passwd|secret|authorization)["']?\s*[:=]\s*["']?)
    ([^\s"',}&]+)
    """
)


def _is_sensitive(key: object) -> bool:
    k = str(key).lower()
    return k in _SENSITIVE_KEYS or k.endswith(("_token", "_password", "_secret"))


def redact(value: Any) -> Any:
    """Return a deep copy of ``value`` with secrets masked (keys and inline text)."""
    if isinstance(value, Mapping):
        return {k: REDACTED if _is_sensitive(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _TOKEN_IN_TEXT.sub(lambda m: f"{m.group(1)}{REDACTED}", value)
    return value


def _redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return {k: REDACTED if _is_sensitive(k) else redact(v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO") -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_processor,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs full request URLs at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger

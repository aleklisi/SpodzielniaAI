"""Structured JSON logging to stdout with redaction of sensitive fields."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from structlog.typing import EventDict, Processor, WrappedLogger

REDACTED = "[REDACTED]"

# A key is sensitive when any of its ``_``/``-`` separated parts is one of these
# (``access_token``, ``Set-Cookie``, ``db_password`` …).
_SENSITIVE_PARTS = frozenset(
    {"authorization", "cookie", "token", "password", "passwd", "secret", "apikey", "kek", "dek"}
)
# Keys redacted only on an exact match, so e.g. ``content_type`` stays visible.
_SENSITIVE_EXACT = frozenset({"content", "body", "api_key", "x_api_key", "x_csrf_token"})
_SPLIT = re.compile(r"[_\-\s.]+")


def is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_EXACT:
        return True
    return any(part in _SENSITIVE_PARTS for part in _SPLIT.split(normalized))


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        mapping: Mapping[object, object] = value  # pyright: ignore[reportUnknownVariableType]
        return {
            k: (REDACTED if isinstance(k, str) and is_sensitive_key(k) else _redact(v))
            for k, v in mapping.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items: Sequence[object] = value  # pyright: ignore[reportUnknownVariableType]
        return [_redact(v) for v in items]
    return value


def redact_sensitive(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor: replace values of sensitive keys (recursively) with ``[REDACTED]``."""
    redacted: MutableMapping[str, Any] = {}
    for key, value in event_dict.items():
        if key != "event" and is_sensitive_key(key):
            redacted[key] = REDACTED
        else:
            redacted[key] = _redact(value)
    return dict(redacted)


def build_processors(*, json: bool = True) -> list[Processor]:
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive,
        renderer,
    ]


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    """Configure structlog (and stdlib logging) to emit one JSON object per line on stdout."""
    log_level = logging.getLevelNamesMapping()[level.upper()]
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level, force=True)
    structlog.configure(
        processors=build_processors(json=json),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger

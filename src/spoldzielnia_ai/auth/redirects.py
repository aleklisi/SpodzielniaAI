"""Open-redirect protection for the post-login ``return_to``."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_RETURN_TO = "/"
_MAX_LENGTH = 2048


def safe_return_to(value: str | None, allowed_origins: Iterable[str]) -> str:
    """A same-origin path to redirect to after login, or ``/``.

    Accepts an absolute path (``/wiki?x=1``) or an absolute URL on an allowed origin, which is
    reduced to its path. Rejects everything else: other origins, scheme-relative ``//host``,
    backslashes (browsers read ``/\\host`` as ``//host``), control characters, oversized values.
    """
    if not value or len(value) > _MAX_LENGTH:
        return DEFAULT_RETURN_TO
    if "\\" in value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return DEFAULT_RETURN_TO
    try:
        parts = urlsplit(value)
    except ValueError:
        return DEFAULT_RETURN_TO

    if parts.scheme or parts.netloc:
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        if parts.scheme != "https" or origin not in {
            o.lower().rstrip("/") for o in allowed_origins
        }:
            return DEFAULT_RETURN_TO
    elif not value.startswith("/") or value.startswith("//"):
        return DEFAULT_RETURN_TO

    path = parts.path or "/"
    if path.startswith("//"):
        return DEFAULT_RETURN_TO
    return urlunsplit(("", "", path, parts.query, parts.fragment))

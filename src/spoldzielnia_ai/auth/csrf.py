"""CSRF protection for unsafe methods (gap G19).

Two independent checks, both required:

1. ``Origin`` (or, if absent, the ``Referer`` origin) must be an allowed origin.
2. Signed double submit: the ``X-CSRF-Token`` header must equal the ``__Host-csrf`` cookie, and
   both must equal ``HMAC(key, sid)`` for the request's session.

``SameSite=Strict`` on the session cookie is a third layer, not a replacement.
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import status
from fastapi.responses import JSONResponse

from spoldzielnia_ai.auth.cookies import CSRF_COOKIE, CSRF_HEADER, SID_COOKIE
from spoldzielnia_ai.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from fastapi import Request, Response

    from spoldzielnia_ai.auth.crypto import CsrfTokens

log = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

type Middleware = Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]


def _origin_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def _refuse(reason: str) -> JSONResponse:
    log.warning("csrf.refused", reason=reason)
    return JSONResponse({"detail": "CSRF check failed"}, status_code=status.HTTP_403_FORBIDDEN)


def csrf_middleware(allowed_origins: Iterable[str], tokens: CsrfTokens) -> Middleware:
    allowed = {o.lower().rstrip("/") for o in allowed_origins}

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in SAFE_METHODS:
            return await call_next(request)

        origin = _origin_of(request.headers.get("origin")) or _origin_of(
            request.headers.get("referer")
        )
        if origin not in allowed:
            return _refuse("origin")

        header = request.headers.get(CSRF_HEADER)
        cookie = request.cookies.get(CSRF_COOKIE)
        sid = request.cookies.get(SID_COOKIE)
        if not header or not cookie or not hmac.compare_digest(header, cookie):
            return _refuse("token_mismatch")
        if not sid or not tokens.matches(sid, header):
            return _refuse("token_not_bound")
        return await call_next(request)

    return middleware

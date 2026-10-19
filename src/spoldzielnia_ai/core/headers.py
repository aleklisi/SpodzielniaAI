"""Security headers on every API response (6.1).

The frontend sets its own, nonce-based CSP. API responses are JSON (or redirects): nothing in
them should ever be rendered as a document, so their CSP allows nothing at all. Caddy adds the
same headers only where an upstream left them out, so these win.

The dev-only Swagger UI (``/docs``) loads scripts from a CDN and is exempt from the CSP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

SECURITY_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}

type Middleware = Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]


def security_headers_middleware(docs_path: str | None) -> Middleware:
    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if docs_path is None or request.url.path != docs_path:
            response.headers.setdefault("Content-Security-Policy", API_CSP)
        return response

    return middleware

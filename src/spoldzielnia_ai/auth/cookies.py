"""Session and CSRF cookies. ``__Host-`` prefix: Secure, ``Path=/``, no ``Domain``, so no
subdomain can set or shadow them."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.responses import Response

SID_COOKIE = "__Host-sid"
CSRF_COOKIE = "__Host-csrf"
CSRF_HEADER = "X-CSRF-Token"


def set_session_cookies(response: Response, sid: str, csrf_token: str) -> None:
    # Browser-session cookies (no Max-Age): the server enforces the absolute lifetime.
    response.set_cookie(SID_COOKIE, sid, path="/", secure=True, httponly=True, samesite="strict")
    # Readable by the frontend, which echoes it in `X-CSRF-Token`.
    response.set_cookie(
        CSRF_COOKIE, csrf_token, path="/", secure=True, httponly=False, samesite="strict"
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SID_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=True, httponly=False, samesite="strict")


def clear_sid_header() -> dict[str, str]:
    """``Set-Cookie`` clearing the session, for ``HTTPException(headers=...)``."""
    return {"Set-Cookie": f'{SID_COOKIE}=""; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=strict'}

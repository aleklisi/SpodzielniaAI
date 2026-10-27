"""``/me``: the authenticated user's own profile, and their UI language (14.1)."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003 - pydantic resolves the annotation at runtime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import update

from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import Role  # noqa: TC001 - pydantic resolves it
from spoldzielnia_ai.db.models import User
from spoldzielnia_ai.i18n import Locale, locale_or_default, set_locale_cookie

router = APIRouter(tags=["me"])
log = get_logger(__name__)

LOCALE_CHANGES_PER_HOUR = 30


class MeResponse(BaseModel):
    id: UUID
    display_name: str | None
    email: str | None
    roles: list[Role]
    mfa_satisfied: bool
    locale: Locale
    locale_chosen: bool


class LocaleIn(BaseModel):
    locale: Locale


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentUser) -> MeResponse:
    return MeResponse(
        id=principal.user.id,
        display_name=principal.user.display_name,
        email=principal.user.email,
        roles=list(principal.user.roles),
        mfa_satisfied=principal.mfa_satisfied,
        locale=locale_or_default(principal.user.locale),
        locale_chosen=principal.user.locale is not None,
    )


async def locale_limit(request: Request, principal: CurrentUser) -> None:
    """A fixed hourly window per user in Redis. Fails open if Redis is down (a preference)."""
    key = f"me:limit:locale:{principal.id}"
    try:
        count = int(await request.app.state.redis.incr(key))
        if count == 1:
            await request.app.state.redis.expire(key, 3600)
    except Exception as exc:
        log.warning("me.rate_limit_unavailable", error=type(exc).__name__)
        return
    if count > LOCALE_CHANGES_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": "rate_limited"},
            headers={"Retry-After": "3600"},
        )


@router.put(
    "/me/locale",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(locale_limit)],
)
async def set_locale(payload: LocaleIn, request: Request, principal: CurrentUser) -> Response:
    """Save the UI language on the account and set the ``lang`` cookie. Not audited: a display
    preference, not a security fact."""
    async with request.app.state.session_factory() as session, session.begin():
        await session.execute(
            update(User).where(User.id == principal.id).values(locale=payload.locale)
        )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    set_locale_cookie(response, payload.locale)
    return response

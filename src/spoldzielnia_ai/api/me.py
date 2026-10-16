"""``/me``: the authenticated user's own profile."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003 - pydantic resolves the annotation at runtime

from fastapi import APIRouter
from pydantic import BaseModel

from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import Role  # noqa: TC001 - pydantic resolves it

router = APIRouter(tags=["me"])


class MeResponse(BaseModel):
    id: UUID
    display_name: str | None
    email: str | None
    roles: list[Role]
    mfa_satisfied: bool


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentUser) -> MeResponse:
    return MeResponse(
        id=principal.user.id,
        display_name=principal.user.display_name,
        email=principal.user.email,
        roles=list(principal.user.roles),
        mfa_satisfied=principal.mfa_satisfied,
    )

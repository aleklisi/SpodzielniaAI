"""Request dependencies that identify the caller."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from uuid import UUID


async def current_user_id() -> UUID:
    """Id of the authenticated user.

    Placeholder until cookie sessions land (1.6): every request is unauthenticated, so routes
    behind it fail closed with 401. Tests override it through ``app.dependency_overrides``.
    """
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

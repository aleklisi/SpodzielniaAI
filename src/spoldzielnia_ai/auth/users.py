"""Reading the authenticated user. The database row, not the session, is the authority for roles
and the lock flag, so an admin lock takes effect on the user's next request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import select

from spoldzielnia_ai.db.models import User

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.db.enums import Role
    from spoldzielnia_ai.db.session import SessionFactory


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    roles: tuple[Role, ...]
    is_locked: bool
    email: str | None
    display_name: str | None


class UserDirectory(Protocol):
    async def get(self, user_id: UUID) -> UserRecord | None: ...


class DbUserDirectory:
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory

    async def get(self, user_id: UUID) -> UserRecord | None:
        async with self._factory() as session:
            user = await session.scalar(select(User).where(User.id == user_id))
        if user is None:
            return None
        return UserRecord(
            id=user.id,
            roles=tuple(user.roles),
            is_locked=user.is_locked,
            email=user.email,
            display_name=user.display_name,
        )

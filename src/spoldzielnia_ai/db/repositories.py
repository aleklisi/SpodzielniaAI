"""Repositories for private-zone tables. Each one accepts only an ``RlsSession``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from spoldzielnia_ai.db.models import PrivateNote

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from spoldzielnia_ai.db.rls import RlsSession


class PrivateNoteRepository:
    """Notes of the session's user. Postgres RLS does the filtering, and no ``WHERE owner_id``
    here could stand in for it. A note owned by someone else looks exactly like a missing one.
    """

    def __init__(self, session: RlsSession) -> None:
        self._session = session

    async def add(self, note: PrivateNote) -> PrivateNote:
        """Insert ``note``. Fails with ``InsufficientPrivilege`` unless it belongs to the user."""
        self._session.add(note)
        await self._session.flush()
        return note

    async def get(self, note_id: UUID) -> PrivateNote | None:
        return await self._session.get(PrivateNote, note_id)

    async def list_all(self) -> Sequence[PrivateNote]:
        result = await self._session.scalars(
            select(PrivateNote).order_by(PrivateNote.created_at.desc(), PrivateNote.id.desc())
        )
        return result.all()

"""Writing to the security audit log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import insert

from spoldzielnia_ai.db.models import SecurityAuditLog

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from pydantic import JsonValue
    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.audit.events import EventType


@dataclass(frozen=True, slots=True)
class AuditTarget:
    """What an event is about, e.g. ``AuditTarget("wiki_article", str(article.id))``."""

    type: str
    id: str


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    hash: bytes


class AuditService:
    """Appends entries in the caller's transaction, so an event commits or rolls back together
    with the change it records.

    The database fills in ``occurred_at``, ``prev_hash`` and ``hash`` (migration 0003). Payloads
    are metadata only: ids, counts, reasons. Never note content, PII or tokens.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        event_type: EventType,
        actor_id: UUID | None,
        target: AuditTarget | None = None,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> AuditEntry:
        stmt = (
            insert(SecurityAuditLog)
            .values(
                event_type=event_type.value,
                actor_id=actor_id,
                target_type=target.type if target else None,
                target_id=target.id if target else None,
                payload=dict(payload or {}),
            )
            .returning(SecurityAuditLog.id, SecurityAuditLog.hash)
        )
        row = (await self._session.execute(stmt)).one()
        return AuditEntry(id=row.id, hash=row.hash)

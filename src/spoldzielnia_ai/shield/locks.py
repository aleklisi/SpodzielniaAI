"""Locking an account for the shield (coverage, honeypots, risk).

The lock is a database flag: ``current_user`` reads it on every request, answers 423 and drops
the account's sessions. Locking, its audit entry and the reason commit together. Only an
admin unlocks (Stage 6 panel).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from sqlalchemy import func

from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.db.models import User
from spoldzielnia_ai.shield.alerts import Alert

if TYPE_CHECKING:
    from uuid import UUID

    from pydantic import JsonValue

    from spoldzielnia_ai.audit.events import EventType
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.shield.alerts import Notifier


class Locker(Protocol):
    async def lock(
        self,
        user_id: UUID,
        reason: str,
        event: EventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> bool: ...

    async def audit(
        self, user_id: UUID, event: EventType, payload: dict[str, JsonValue] | None = None
    ) -> None: ...


class AccountLocker:
    def __init__(self, factory: SessionFactory, notifier: Notifier) -> None:
        self._factory = factory
        self._notifier = notifier

    async def lock(
        self,
        user_id: UUID,
        reason: str,
        event: EventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> bool:
        """Lock ``user_id`` and record ``event``. False if it was locked already (no new audit
        entry or alert: one incident, one record)."""
        detail: dict[str, JsonValue] = {"reason": reason, **(payload or {})}
        async with self._factory() as session, session.begin():
            user = await session.get(User, user_id, with_for_update=True)
            if user is None or user.is_locked:
                return False
            user.is_locked = True
            user.locked_reason = reason
            user.locked_at = func.now()
            await AuditService(session).record(
                event, user_id, AuditTarget("app_user", str(user_id)), detail
            )
        await self._notifier.notify(Alert(event.value, user_id, detail))
        return True

    async def audit(
        self, user_id: UUID, event: EventType, payload: dict[str, JsonValue] | None = None
    ) -> None:
        """Record ``event`` about ``user_id`` without locking (e.g. a 24h throttle)."""
        async with self._factory() as session, session.begin():
            await AuditService(session).record(
                event, user_id, AuditTarget("app_user", str(user_id)), payload or {}
            )

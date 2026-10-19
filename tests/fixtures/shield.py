"""Fakes for shield tests: a fixed corpus size and a locker that records what it was asked."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from spoldzielnia_ai.shield.coverage import CorpusSize

if TYPE_CHECKING:
    from uuid import UUID

    from pydantic import JsonValue

    from spoldzielnia_ai.audit.events import EventType


@dataclass
class FixedCorpus:
    blocks: int
    articles: int = 1

    async def size(self) -> CorpusSize:
        return CorpusSize(self.blocks, self.articles)


@dataclass
class FakeLocker:
    locked: list[tuple[UUID, str, EventType]] = field(
        default_factory=list["tuple[UUID, str, EventType]"]
    )
    audited: list[tuple[UUID, EventType, dict[str, JsonValue]]] = field(
        default_factory=list["tuple[UUID, EventType, dict[str, JsonValue]]"]
    )

    async def lock(
        self,
        user_id: UUID,
        reason: str,
        event: EventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> bool:
        first = all(u != user_id for u, _, _ in self.locked)
        self.locked.append((user_id, reason, event))
        return first

    async def audit(
        self, user_id: UUID, event: EventType, payload: dict[str, JsonValue] | None = None
    ) -> None:
        self.audited.append((user_id, event, payload or {}))

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, FetchedValue, Identity, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Base


class SecurityAuditLog(Base):
    """Append-only, hash-chained security log (gap G10).

    ``prev_hash`` and ``hash`` are computed by a ``BEFORE INSERT`` trigger, and the table is made
    immutable by revokes and a trigger in 1.4. ``actor_id`` has no FK, so audit rows outlive
    the users they mention.
    """

    __tablename__ = "security_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    prev_hash: Mapped[bytes] = mapped_column(server_default=FetchedValue())
    hash: Mapped[bytes] = mapped_column(server_default=FetchedValue())

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Identity, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import OutboxStatus, VectorOpKind


class VectorOutbox(Entity):
    """A canonical-index operation committed together with the change that requires it (4.4).

    Applied in ``seq`` order, right after commit and otherwise by the reconciler. ``seq`` comes
    from a sequence at insert time: a change that waited for another's row locks always gets a
    higher one, so conflicting operations apply in commit order. ``filter_json`` and
    ``payload_json`` hold ids and governance fields only.
    """

    __tablename__ = "vector_outbox"
    __table_args__ = (Index(None, "status", "seq"),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    op: Mapped[VectorOpKind]
    filter_json: Mapped[dict[str, Any]]
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"), default=dict
    )
    status: Mapped[OutboxStatus] = mapped_column(
        server_default=OutboxStatus.PENDING.value, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(server_default=text("0"), default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(String(64))
    alerted: Mapped[bool] = mapped_column(server_default=text("false"), default=False)

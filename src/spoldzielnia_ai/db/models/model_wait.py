from __future__ import annotations

from uuid import UUID

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity


class ModelWait(Entity):
    """Work parked until a model is configured for ``purpose`` (10.2, P5).

    Ids only, like the vector outbox: the work itself (a derivative under RLS, an article) is
    untouched. The resume task reads this table, which needs no RLS bypass, and re-queues each
    item as its owner once the purpose has a usable model. ``task`` is the ``TaskName`` to send
    again; ``owner_id`` is set for private work (the RLS session to resume it in).
    """

    __tablename__ = "model_wait"
    __table_args__ = (UniqueConstraint("subject_id", "task"),)

    subject_id: Mapped[UUID]
    owner_id: Mapped[UUID | None]
    purpose: Mapped[str] = mapped_column(String(32), index=True)
    task: Mapped[str] = mapped_column(String(64))

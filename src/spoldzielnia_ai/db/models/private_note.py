from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity


class PrivateNote(Entity):
    """A note in a member's private notebook.

    The body is stored only as AES-256-GCM ciphertext under a wrapped data key (2.1). Access is
    isolated per owner by Postgres RLS on ``owner_id`` (1.4).
    """

    __tablename__ = "private_note"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    body_ciphertext: Mapped[bytes]
    body_nonce: Mapped[bytes]
    dek_wrapped: Mapped[bytes]
    source_type: Mapped[str] = mapped_column(String(32))

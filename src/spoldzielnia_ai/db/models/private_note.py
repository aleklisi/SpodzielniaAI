from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from spoldzielnia_ai.db.base import Entity
from spoldzielnia_ai.db.enums import DerivativeKind, DerivativeStatus, ScanStatus


class PrivateNote(Entity):
    """A note in a member's private notebook.

    Title and body are stored only as AES-256-GCM ciphertext under the row's own wrapped data key
    (``core.crypto``, 2.1). Access is isolated per owner by Postgres RLS on ``owner_id`` (1.4).
    """

    __tablename__ = "private_note"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    title_ciphertext: Mapped[bytes]
    body_ciphertext: Mapped[bytes]
    dek_wrapped: Mapped[bytes]
    source_type: Mapped[str] = mapped_column(String(32))


class NoteAttachment(Entity):
    """A file attached to a note. The object in storage is client-side encrypted under this
    row's DEK (segmented AEAD) and MinIO adds SSE as a second layer. ``object_key`` is random,
    and the original filename exists only as ciphertext."""

    __tablename__ = "note_attachment"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    note_id: Mapped[UUID] = mapped_column(
        ForeignKey("private_note.id", ondelete="CASCADE"), index=True
    )
    object_key: Mapped[str] = mapped_column(String(64), unique=True)
    mime_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(BigInteger)  # plaintext bytes after sanitising
    sha256: Mapped[str] = mapped_column(String(64))  # hex, of the stored plaintext
    scan_status: Mapped[ScanStatus] = mapped_column(
        server_default=ScanStatus.PENDING.value, default=ScanStatus.PENDING
    )
    keep_location: Mapped[bool] = mapped_column(server_default=text("false"), default=False)
    filename_ciphertext: Mapped[bytes]
    dek_wrapped: Mapped[bytes]


class NoteDerivative(Entity):
    """Text derived from a note's attachment or link (transcript, description, parsed document,
    fetched page). Encrypted like the note. Stage 3 scans it for prompt injection."""

    __tablename__ = "note_derivative"

    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    note_id: Mapped[UUID] = mapped_column(
        ForeignKey("private_note.id", ondelete="CASCADE"), index=True
    )
    # NULL for FETCHED_LINK, which has no attachment.
    attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("note_attachment.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[DerivativeKind]
    status: Mapped[DerivativeStatus] = mapped_column(
        server_default=DerivativeStatus.PENDING.value, default=DerivativeStatus.PENDING
    )
    source_ciphertext: Mapped[bytes | None]  # e.g. the URL of a FETCHED_LINK
    content_ciphertext: Mapped[bytes | None]
    dek_wrapped: Mapped[bytes]
    error: Mapped[str | None] = mapped_column(String(64))  # machine-readable reason, no content
    # Kinds, rule names, offsets and scores only (3.3), never content.
    injection_flags: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("'{}'::jsonb"), default=dict
    )
    quarantine_confirmed_at: Mapped[datetime | None]

"""Encrypted note storage on top of an ``RlsSession``.

Only this module sees plaintext note fields. Rows hold ciphertext under their own DEK, bound to
``table:row_id:owner_id``. Ownership is enforced by Postgres RLS: a note of another user is
indistinguishable from a missing one (``None``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - dataclass field types
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - dataclass field types

from sqlalchemy import select

from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.db.models import NoteAttachment, NoteDerivative, PrivateNote

if TYPE_CHECKING:
    from collections.abc import Sequence

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.enums import DerivativeKind, DerivativeStatus, ScanStatus
    from spoldzielnia_ai.db.rls import RlsSession

NOTE = "private_note"
ATTACHMENT = "note_attachment"
DERIVATIVE = "note_derivative"


@dataclass(frozen=True, slots=True)
class NoteSummary:
    id: UUID
    title: str
    source_type: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AttachmentView:
    id: UUID
    note_id: UUID
    filename: str
    mime_type: str
    size: int
    scan_status: ScanStatus
    keep_location: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DerivativeView:
    id: UUID
    attachment_id: UUID | None
    kind: DerivativeKind
    status: DerivativeStatus
    source: str | None
    content: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NoteView:
    id: UUID
    title: str
    body: str
    source_type: str
    created_at: datetime
    updated_at: datetime
    attachments: Sequence[AttachmentView]
    derivatives: Sequence[DerivativeView]


class NotebookService:
    def __init__(self, session: RlsSession, cipher: EnvelopeCipher) -> None:
        self._session = session
        self._cipher = cipher

    # --- Decryption helpers -------------------------------------------------------------

    def _note_fields(self, note: PrivateNote) -> tuple[str, str]:
        aad = row_aad(NOTE, note.id, note.owner_id)
        dek = self._cipher.unwrap(note.dek_wrapped, aad)
        title = self._cipher.decrypt_field(dek, note.title_ciphertext, aad, "title")
        body = self._cipher.decrypt_field(dek, note.body_ciphertext, aad, "body")
        return title.decode(), body.decode()

    def attachment_view(self, row: NoteAttachment) -> AttachmentView:
        aad = row_aad(ATTACHMENT, row.id, row.owner_id)
        dek = self._cipher.unwrap(row.dek_wrapped, aad)
        filename = self._cipher.decrypt_field(dek, row.filename_ciphertext, aad, "filename")
        return AttachmentView(
            id=row.id,
            note_id=row.note_id,
            filename=filename.decode(),
            mime_type=row.mime_type,
            size=row.size,
            scan_status=row.scan_status,
            keep_location=row.keep_location,
            created_at=row.created_at,
        )

    def derivative_view(self, row: NoteDerivative) -> DerivativeView:
        aad = row_aad(DERIVATIVE, row.id, row.owner_id)
        dek = self._cipher.unwrap(row.dek_wrapped, aad)

        def field(blob: bytes | None, name: str) -> str | None:
            if blob is None:
                return None
            return self._cipher.decrypt_field(dek, blob, aad, name).decode()

        return DerivativeView(
            id=row.id,
            attachment_id=row.attachment_id,
            kind=row.kind,
            status=row.status,
            source=field(row.source_ciphertext, "source"),
            content=field(row.content_ciphertext, "content"),
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # --- Notes --------------------------------------------------------------------------

    async def create(
        self, owner_id: UUID, title: str, body: str, source_type: str = "text"
    ) -> NoteView:
        note_id = uuid7()
        aad = row_aad(NOTE, note_id, owner_id)
        key = self._cipher.new_key(aad)
        note = PrivateNote(
            id=note_id,
            owner_id=owner_id,
            title_ciphertext=self._cipher.encrypt_field(key.key, title.encode(), aad, "title"),
            body_ciphertext=self._cipher.encrypt_field(key.key, body.encode(), aad, "body"),
            dek_wrapped=key.wrapped,
            source_type=source_type,
        )
        self._session.add(note)
        await self._session.flush()
        await self._session.refresh(note)
        return NoteView(note.id, title, body, source_type, note.created_at, note.updated_at, [], [])

    async def _note(self, note_id: UUID) -> PrivateNote | None:
        return await self._session.get(PrivateNote, note_id)

    async def exists(self, note_id: UUID) -> bool:
        return await self._note(note_id) is not None

    async def list(self) -> list[NoteSummary]:
        notes = await self._session.scalars(
            select(PrivateNote).order_by(PrivateNote.updated_at.desc(), PrivateNote.id.desc())
        )
        summaries: list[NoteSummary] = []
        for note in notes:
            title, _ = self._note_fields(note)
            summaries.append(
                NoteSummary(note.id, title, note.source_type, note.created_at, note.updated_at)
            )
        return summaries

    async def get(self, note_id: UUID) -> NoteView | None:
        note = await self._note(note_id)
        if note is None:
            return None
        title, body = self._note_fields(note)
        attachments = await self._session.scalars(
            select(NoteAttachment)
            .where(NoteAttachment.note_id == note_id)
            .order_by(NoteAttachment.created_at, NoteAttachment.id)
        )
        derivatives = await self._session.scalars(
            select(NoteDerivative)
            .where(NoteDerivative.note_id == note_id)
            .order_by(NoteDerivative.created_at, NoteDerivative.id)
        )
        return NoteView(
            note.id,
            title,
            body,
            note.source_type,
            note.created_at,
            note.updated_at,
            [self.attachment_view(a) for a in attachments],
            [self.derivative_view(d) for d in derivatives],
        )

    async def get_attachment(
        self, note_id: UUID, attachment_id: UUID
    ) -> tuple[AttachmentView, list[DerivativeView]] | None:
        row = await self._session.scalar(
            select(NoteAttachment).where(
                NoteAttachment.id == attachment_id, NoteAttachment.note_id == note_id
            )
        )
        if row is None:
            return None
        derivatives = await self._session.scalars(
            select(NoteDerivative)
            .where(NoteDerivative.attachment_id == attachment_id)
            .order_by(NoteDerivative.created_at, NoteDerivative.id)
        )
        return self.attachment_view(row), [self.derivative_view(d) for d in derivatives]

    async def update(
        self, note_id: UUID, title: str | None = None, body: str | None = None
    ) -> NoteView | None:
        note = await self._note(note_id)
        if note is None:
            return None
        aad = row_aad(NOTE, note.id, note.owner_id)
        dek = self._cipher.unwrap(note.dek_wrapped, aad)
        if title is not None:
            note.title_ciphertext = self._cipher.encrypt_field(dek, title.encode(), aad, "title")
        if body is not None:
            note.body_ciphertext = self._cipher.encrypt_field(dek, body.encode(), aad, "body")
        await self._session.flush()
        await self._session.refresh(note)
        return await self.get(note_id)

    async def delete(self, note_id: UUID) -> list[str] | None:
        """Delete the note (attachments and derivatives cascade). Returns the storage keys the
        caller must remove after commit, or ``None`` if there is no such note."""
        note = await self._note(note_id)
        if note is None:
            return None
        keys = list(
            await self._session.scalars(
                select(NoteAttachment.object_key).where(NoteAttachment.note_id == note_id)
            )
        )
        await self._session.delete(note)
        await self._session.flush()
        return keys

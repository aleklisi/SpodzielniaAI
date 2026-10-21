"""Image bytes of private attachments for display (chunk 9.2).

Only the owner gets them: rows are read through the owner's RLS session, so another member's
attachment is a 404, indistinguishable from a missing one. The preview rendition is encrypted
under the attachment's DEK with its own AAD (``…|preview``), so it can't be swapped for another
object; it is created on upload, or on first view for photos uploaded before 9.2.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.db.models import NoteAttachment
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.files.preview import (
    BROWSER_FORMATS,
    FULL_SIDE,
    PREVIEW_SIDE,
    Rendition,
    render_jpeg,
)
from spoldzielnia_ai.files.storage import new_object_key
from spoldzielnia_ai.notebook.service import ATTACHMENT

if TYPE_CHECKING:
    from uuid import UUID

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.storage import ObjectStorage

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/heic"})


def preview_aad(attachment_id: UUID, owner_id: UUID) -> str:
    return f"{row_aad(ATTACHMENT, attachment_id, owner_id)}|preview"


@dataclass(frozen=True, slots=True)
class ImageBytes:
    data: bytes
    mime_type: str


@dataclass(frozen=True, slots=True)
class SealedPreview:
    object_key: str
    width: int
    height: int


async def seal_preview(
    cipher: EnvelopeCipher,
    storage: ObjectStorage,
    rendition: Rendition,
    attachment_id: UUID,
    owner_id: UUID,
    dek: bytes,
) -> SealedPreview:
    """Encrypt and store a preview rendition; the caller records its key on the row (and
    deletes the object if that fails)."""
    sealed = io.BytesIO()

    def seal() -> int:
        return cipher.encrypt_stream(
            dek, io.BytesIO(rendition.data), sealed, preview_aad(attachment_id, owner_id)
        )

    size = await asyncio.to_thread(seal)
    sealed.seek(0)
    key = new_object_key()
    await storage.put(key, sealed, size)
    return SealedPreview(key, rendition.width, rendition.height)


class PrivateImages:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        storage: ObjectStorage,
        max_pixels: int,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._storage = storage
        self._max_pixels = max_pixels

    async def get(
        self, owner_id: UUID, note_id: UUID, attachment_id: UUID, *, full: bool
    ) -> ImageBytes | None:
        async with rls_session(self._factory, owner_id) as session:
            row = await session.scalar(
                select(NoteAttachment).where(
                    NoteAttachment.id == attachment_id,
                    NoteAttachment.note_id == note_id,
                    NoteAttachment.mime_type.in_(IMAGE_TYPES),
                )
            )
        if row is None:
            return None
        aad = row_aad(ATTACHMENT, row.id, row.owner_id)
        dek = self._cipher.unwrap(row.dek_wrapped, aad)
        if not full and row.preview_object_key is not None:
            data = await self._read(row.preview_object_key, dek, preview_aad(row.id, row.owner_id))
            return ImageBytes(data, "image/jpeg")
        original = await self._read(row.object_key, dek, aad)
        if full and row.mime_type in BROWSER_FORMATS:
            return ImageBytes(original, row.mime_type)
        if full:  # HEIC: browsers can't show it
            rendition = await asyncio.to_thread(self._render, original, FULL_SIDE)
            return ImageBytes(rendition.data, "image/jpeg")
        return ImageBytes(await self._backfill(row, original, dek), "image/jpeg")

    def _render(self, original: bytes, side: int) -> Rendition:
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".img") as handle:
            handle.write(original)
            handle.flush()
            return render_jpeg(Path(handle.name), side, self._max_pixels)

    async def _read(self, key: str, dek: bytes, aad: str) -> bytes:
        sealed = io.BytesIO()
        await self._storage.get(key, sealed)
        sealed.seek(0)

        def decrypt() -> bytes:
            return b"".join(self._cipher.decrypt_stream(dek, sealed, aad))

        return await asyncio.to_thread(decrypt)

    async def _backfill(self, row: NoteAttachment, original: bytes, dek: bytes) -> bytes:
        """A photo stored before 9.2: create its preview now and remember the image size."""
        rendition = await asyncio.to_thread(self._render, original, PREVIEW_SIDE)
        sealed = await seal_preview(
            self._cipher, self._storage, rendition, row.id, row.owner_id, dek
        )
        async with rls_session(self._factory, row.owner_id) as session:
            current = await session.get(NoteAttachment, row.id, with_for_update=True)
            won = current is not None and current.preview_object_key is None
            if current is not None and won:
                current.preview_object_key = sealed.object_key
                current.width, current.height = sealed.width, sealed.height
        if not won:  # deleted meanwhile, or a concurrent view stored one first
            await self._storage.delete(sealed.object_key)
        return rendition.data

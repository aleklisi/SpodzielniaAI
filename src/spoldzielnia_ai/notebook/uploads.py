"""Attachment upload pipeline (chunk 2.3).

Order (each failure audited as ``UPLOAD_REJECTED`` with a reason code, never content):

1. note ownership (RLS) — checked by the router before the body is read (404)
2. streamed spool with a size cap (413) — ``files.multipart``
3. type validation by content (415/413) and 4. archive guard (422) — ``files.validation``
5. ClamAV (422 infected, 503 scanner down; fail-closed)
6. metadata stripping — ``files.sanitize``
7. client-side encryption → object storage (+ an encrypted JPEG preview for images, 9.2) →
   ``NoteAttachment(scan_status=CLEAN)``
8. processing task enqueued (2.5)

No database transaction is held while bytes stream or scan: the row is inserted in a short RLS
transaction at the end, which re-checks the note (it may have been deleted meanwhile).
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ScanStatus
from spoldzielnia_ai.db.models import NoteAttachment, PrivateNote
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.files.antivirus import ScannerUnavailable
from spoldzielnia_ai.files.preview import PREVIEW_SIDE, render_jpeg
from spoldzielnia_ai.files.sanitize import sanitize
from spoldzielnia_ai.files.storage import new_object_key
from spoldzielnia_ai.files.validation import FileRejected, validate_file
from spoldzielnia_ai.notebook.images import SealedPreview, seal_preview
from spoldzielnia_ai.notebook.service import ATTACHMENT, NotebookService

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from pydantic import JsonValue

    from spoldzielnia_ai.auth.sessions import AuditSink
    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import UploadSettings
    from spoldzielnia_ai.db.enums import DerivativeKind
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.antivirus import VirusScanner
    from spoldzielnia_ai.files.multipart import SpooledUpload
    from spoldzielnia_ai.files.storage import ObjectStorage
    from spoldzielnia_ai.files.validation import FileKind
    from spoldzielnia_ai.notebook.service import AttachmentView
    from spoldzielnia_ai.workers.jobs import AtlasJob, DerivativeJob

log = get_logger(__name__)


class UploadRejected(Exception):
    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class ProcessingQueue(Protocol):
    """Hands work to the media workers (2.5). Only ids cross the boundary, never content."""

    async def attachment_stored(
        self, attachment_id: UUID, owner_id: UUID, kind: FileKind
    ) -> list[DerivativeKind]: ...

    async def link_added(self, derivative_id: UUID, owner_id: UUID) -> None: ...

    async def annotations_saved(self, job: DerivativeJob) -> None: ...

    async def atlas_requested(self, job: AtlasJob) -> None: ...

    async def promotion_requested(self, promotion_id: UUID, owner_id: UUID) -> None: ...

    async def article_canonical(self, article_id: UUID) -> None: ...


class NoProcessing:
    async def attachment_stored(
        self, attachment_id: UUID, owner_id: UUID, kind: FileKind
    ) -> list[DerivativeKind]:
        return []

    async def link_added(self, derivative_id: UUID, owner_id: UUID) -> None:
        return None

    async def annotations_saved(self, job: DerivativeJob) -> None:
        return None

    async def atlas_requested(self, job: AtlasJob) -> None:
        return None

    async def promotion_requested(self, promotion_id: UUID, owner_id: UUID) -> None:
        return None

    async def article_canonical(self, article_id: UUID) -> None:
        return None


def _sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


class UploadPipeline:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        storage: ObjectStorage,
        scanner: VirusScanner,
        settings: UploadSettings,
        audit: AuditSink,
        queue: ProcessingQueue,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._storage = storage
        self._scanner = scanner
        self._settings = settings
        self._audit = audit
        self._queue = queue

    async def reject(
        self, owner_id: UUID, note_id: UUID, code: str, status: int, **extra: JsonValue
    ) -> UploadRejected:
        """Audit (own transaction, so it survives the failed request) and build the error."""
        payload: Mapping[str, JsonValue] = {"reason": code, "note": str(note_id), **extra}
        await self._audit(EventType.UPLOAD_REJECTED, owner_id, payload)
        log.info("upload.rejected", reason=code, status=status)
        return UploadRejected(code, status)

    async def note_exists(self, owner_id: UUID, note_id: UUID) -> bool:
        async with rls_session(self._factory, owner_id) as session:
            return await session.get(PrivateNote, note_id) is not None

    async def store(
        self, owner_id: UUID, note_id: UUID, upload: SpooledUpload, keep_location: bool
    ) -> AttachmentView:
        workdir = Path(tempfile.mkdtemp(prefix="att-", dir=self._settings.tmp_dir))
        try:
            return await self._store(owner_id, note_id, upload, keep_location, workdir)
        finally:
            await asyncio.to_thread(_rmtree, workdir)

    async def _store(
        self,
        owner_id: UUID,
        note_id: UUID,
        upload: SpooledUpload,
        keep_location: bool,
        workdir: Path,
    ) -> AttachmentView:
        # 3./4. Type, extension, size per category, archive guard, macros.
        try:
            kind = await asyncio.to_thread(
                validate_file, upload.path, upload.filename, upload.content_type
            )
        except FileRejected as exc:
            raise await self.reject(
                owner_id, note_id, exc.code, exc.status, size=upload.size
            ) from None

        # 5. Antivirus, fail-closed.
        try:
            with upload.path.open("rb") as handle:
                verdict = await self._scanner.scan(handle)
        except ScannerUnavailable as exc:
            log.error("upload.scanner_unavailable", error=str(exc))
            raise await self.reject(owner_id, note_id, "scanner_unavailable", 503) from None
        if not verdict.clean:
            raise await self.reject(
                owner_id, note_id, "infected", 422, signature=verdict.signature, mime=kind.mime
            )

        # 6. Metadata.
        clean_path = workdir / "clean"
        try:
            result = await asyncio.to_thread(
                sanitize,
                upload.path,
                clean_path,
                kind,
                keep_location=keep_location,
                max_pixels=self._settings.max_image_pixels,
            )
        except FileRejected as exc:
            raise await self.reject(
                owner_id, note_id, exc.code, exc.status, mime=kind.mime
            ) from None
        upload.cleanup()  # only the sanitised copy is kept from here on
        size, sha256 = await asyncio.to_thread(_sha256, result.path)

        # 7. Encrypt and store, then the row.
        attachment_id = uuid7()
        aad = row_aad(ATTACHMENT, attachment_id, owner_id)
        key = self._cipher.new_key(aad)
        sealed_path = workdir / "sealed"

        def seal() -> int:
            with result.path.open("rb") as src, sealed_path.open("wb") as dst:
                return self._cipher.encrypt_stream(key.key, src, dst, aad)

        sealed_size = await asyncio.to_thread(seal)
        object_key = new_object_key()
        with sealed_path.open("rb") as sealed:
            await self._storage.put(object_key, sealed, sealed_size)
        preview: SealedPreview | None = None
        if kind.category == "image":  # display rendition for the photo editor (9.2)
            try:
                rendition = await asyncio.to_thread(
                    render_jpeg, result.path, PREVIEW_SIDE, self._settings.max_image_pixels
                )
                preview = await seal_preview(
                    self._cipher, self._storage, rendition, attachment_id, owner_id, key.key
                )
            except BaseException:
                await self._storage.delete(object_key)
                raise

        row = NoteAttachment(
            id=attachment_id,
            owner_id=owner_id,
            note_id=note_id,
            object_key=object_key,
            mime_type=kind.mime,
            size=size,
            sha256=sha256,
            scan_status=ScanStatus.CLEAN,
            keep_location=result.location_retained,
            filename_ciphertext=self._cipher.encrypt_field(
                key.key, upload.filename.encode(), aad, "filename"
            ),
            dek_wrapped=key.wrapped,
            width=preview.width if preview else None,
            height=preview.height if preview else None,
            preview_object_key=preview.object_key if preview else None,
        )
        try:
            async with rls_session(self._factory, owner_id) as session:
                if await session.get(PrivateNote, note_id) is None:
                    raise UploadRejected("note_not_found", 404)
                session.add(row)
                await session.flush()
                await session.refresh(row)
                if result.location_retained:
                    await AuditService(session).record(
                        EventType.ATTACHMENT_LOCATION_RETAINED,
                        owner_id,
                        AuditTarget(ATTACHMENT, str(attachment_id)),
                        {"note": str(note_id)},
                    )
                view = NotebookService(session, self._cipher).attachment_view(row)
        except BaseException:
            await self._storage.delete(object_key)  # no orphaned object
            if preview is not None:
                await self._storage.delete(preview.object_key)
            raise

        # 8. Processing (2.5).
        await self._queue.attachment_stored(attachment_id, owner_id, kind)
        return view


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)

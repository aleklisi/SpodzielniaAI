"""What a worker does with a ``DerivativeJob`` (independent of Celery, so it is testable).

1. Load the derivative (and its attachment) through ``rls_session(owner_id)`` and mark it
   PROCESSING. A missing row (note deleted meanwhile) or a DONE one (redelivery) is a no-op.
2. Outside any transaction: download and decrypt the attachment into the worker's tmpfs, run
   the engine in a thread (or fetch the link in the egress worker).
3. Encrypt the text into the derivative and mark it DONE.

Failures: ``PermanentError`` → FAILED with a code (shown to the user) + ``PROCESSING_FAILED``
audit. ``TransientError`` → retried with backoff by the caller. After the last attempt it
becomes FAILED ``retries_exhausted``. ``WaitingForModel`` (10.2) → WAITING_FOR_MODEL plus a
``model_wait`` row; ``workers.waiting`` sends the job again once the purpose has a model.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.crypto import InvalidTag, row_aad
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import DerivativeKind, DerivativeStatus, ScanStatus
from spoldzielnia_ai.db.models import NoteAttachment, NoteDerivative
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.files.multipart import SpooledUpload, safe_filename
from spoldzielnia_ai.files.pdf_hidden import HiddenSpan, remove_hidden
from spoldzielnia_ai.llm.injection_rules import count_invisible, find_rules, strip_invisible
from spoldzielnia_ai.net.safe_fetch import FetchFailed, SsrfBlocked
from spoldzielnia_ai.net.to_markdown import to_markdown
from spoldzielnia_ai.notebook.service import ATTACHMENT, DERIVATIVE
from spoldzielnia_ai.notebook.uploads import UploadRejected
from spoldzielnia_ai.workers.engines.base import PermanentError, TransientError, WaitingForModel
from spoldzielnia_ai.workers.waiting import park_derivative

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.storage import ObjectStorage
    from spoldzielnia_ai.net.safe_fetch import SafeFetcher
    from spoldzielnia_ai.notebook.uploads import UploadPipeline
    from spoldzielnia_ai.workers.engines.base import Engine
    from spoldzielnia_ai.workers.jobs import DerivativeJob

log = get_logger(__name__)

MAX_CONTENT_CHARS = 2_000_000
# Engines read formats by extension (Docling) or sniff them (PyAV): give files a proper suffix.
SUFFIX = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "video/mp4": ".mp4",
}
_TRANSIENT_FETCH = ("dns_failed", "connection_failed")


@dataclass(frozen=True, slots=True)
class _Source:
    derivative_id: UUID
    owner_id: UUID
    note_id: UUID
    kind: DerivativeKind
    attachment_id: UUID | None
    object_key: str | None
    mime_type: str | None
    attachment_dek: bytes | None
    url: str | None


class DerivativeProcessor:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        storage: ObjectStorage,
        engines: Callable[[DerivativeKind, str], Engine],
        tmp_dir: str | None = None,
        fetcher: SafeFetcher | None = None,
        uploads: UploadPipeline | None = None,
        on_done: Callable[[DerivativeJob], Awaitable[None]] | None = None,
    ) -> None:
        self._on_done = on_done
        self._factory = factory
        self._cipher = cipher
        self._storage = storage
        self._engines = engines
        self._tmp_dir = tmp_dir
        self._fetcher = fetcher
        self._uploads = uploads

    async def handle(self, job: DerivativeJob, final_attempt: bool) -> bool:
        """Process ``job``. Returns True if the caller should retry later."""
        source = await self._claim(job)
        if source is None:
            return False
        try:
            content, attachment_id, document = await self._produce(source)
        except PermanentError as exc:
            await self._fail(source, exc.code)
            return False
        except WaitingForModel as exc:
            await park_derivative(self._factory, source.derivative_id, source.owner_id, exc.purpose)
            return False
        except TransientError as exc:
            if final_attempt:
                await self._fail(source, "retries_exhausted")
                return False
            log.warning("worker.transient", derivative=str(source.derivative_id), error=str(exc))
            return True
        await self._complete(source, content, attachment_id, document)
        if self._on_done is not None:
            await self._on_done(job)  # prompt-injection screening in worker-core (3.3)
        return False

    async def fail_timeout(self, job: DerivativeJob) -> None:
        source = await self._claim(job, allow_processing=True)
        if source is not None:
            await self._fail(source, "timeout")

    # --- Database steps (short RLS transactions) ----------------------------------------

    async def _claim(self, job: DerivativeJob, allow_processing: bool = True) -> _Source | None:
        async with rls_session(self._factory, job.owner_id) as session:
            row = await session.get(NoteDerivative, job.derivative_id, with_for_update=True)
            if row is None or row.owner_id != job.owner_id:
                log.info("worker.derivative_gone", derivative=str(job.derivative_id))
                return None
            if row.status in (
                DerivativeStatus.DONE,
                DerivativeStatus.FAILED,
                DerivativeStatus.QUARANTINED,
            ):
                return None  # redelivered after completion
            if row.status == DerivativeStatus.PROCESSING and not allow_processing:
                return None
            row.status = DerivativeStatus.PROCESSING
            row.error = None

            url: str | None = None
            if row.source_ciphertext is not None:
                aad = row_aad(DERIVATIVE, row.id, row.owner_id)
                dek = self._cipher.unwrap(row.dek_wrapped, aad)
                url = self._cipher.decrypt_field(dek, row.source_ciphertext, aad, "source").decode()

            object_key = mime = None
            attachment_dek: bytes | None = None
            if row.attachment_id is not None:
                attachment = await session.get(NoteAttachment, row.attachment_id)
                if attachment is None:
                    return None
                if attachment.scan_status != ScanStatus.CLEAN:
                    row.status = DerivativeStatus.FAILED
                    row.error = "not_scanned"
                    return None
                aad = row_aad(ATTACHMENT, attachment.id, attachment.owner_id)
                attachment_dek = self._cipher.unwrap(attachment.dek_wrapped, aad)
                object_key, mime = attachment.object_key, attachment.mime_type
            return _Source(
                derivative_id=row.id,
                owner_id=row.owner_id,
                note_id=row.note_id,
                kind=row.kind,
                attachment_id=row.attachment_id,
                object_key=object_key,
                mime_type=mime,
                attachment_dek=attachment_dek,
                url=url,
            )

    async def _complete(
        self,
        source: _Source,
        content: str,
        attachment_id: UUID | None,
        document: dict[str, Any],
    ) -> None:
        async with rls_session(self._factory, source.owner_id) as session:
            row = await session.get(NoteDerivative, source.derivative_id, with_for_update=True)
            if row is None:
                return  # note deleted while we worked
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = self._cipher.unwrap(row.dek_wrapped, aad)
            text = content[:MAX_CONTENT_CHARS]
            row.content_ciphertext = self._cipher.encrypt_field(dek, text.encode(), aad, "content")
            row.status = DerivativeStatus.DONE
            row.error = None
            # Parsing findings (no text). worker-core adds the injection verdict.
            row.injection_flags = {"document": document}
            if attachment_id is not None:
                row.attachment_id = attachment_id
        log.info("worker.done", derivative=str(source.derivative_id), kind=source.kind.value)

    async def _fail(self, source: _Source, code: str) -> None:
        async with rls_session(self._factory, source.owner_id) as session:
            row = await session.get(NoteDerivative, source.derivative_id, with_for_update=True)
            if row is None:
                return
            row.status = DerivativeStatus.FAILED
            row.error = code[:64]
            await AuditService(session).record(
                EventType.PROCESSING_FAILED,
                source.owner_id,
                AuditTarget(DERIVATIVE, str(source.derivative_id)),
                {"kind": source.kind.value, "error": code[:64]},
            )
        log.warning("worker.failed", derivative=str(source.derivative_id), error=code)

    # --- Work ---------------------------------------------------------------------------

    async def _produce(self, source: _Source) -> tuple[str, UUID | None, dict[str, Any]]:
        document: dict[str, Any] = {}
        if source.kind == DerivativeKind.FETCHED_LINK:
            text, attachment_id = await self._fetch_link(source)
        else:
            text, attachment_id = await self._extract(source, document), None
        # Zero-width and bidi controls hide or reorder text for humans only (3.3).
        document["invisible_chars"] = count_invisible(text)
        return strip_invisible(text), attachment_id, document

    async def _extract(self, source: _Source, document: dict[str, Any]) -> str:
        if source.object_key is None or source.mime_type is None or source.attachment_dek is None:
            raise PermanentError("attachment_missing")
        engine = self._engines(source.kind, source.mime_type)
        workdir = Path(tempfile.mkdtemp(prefix="job-", dir=self._tmp_dir))
        try:
            plain = workdir / f"input{SUFFIX.get(source.mime_type, '')}"
            await self._materialize(source, workdir, plain)
            hidden = await self._hidden_text(plain, source.mime_type)
            text = await asyncio.to_thread(engine.extract, plain, source.mime_type)
            if hidden:
                text, removed = remove_hidden(text, hidden)
                document["hidden_text"] = [
                    {
                        "page": span.page,
                        "reason": span.reason,
                        "chars": len(span.text),
                        "instruction_like": bool(find_rules(span.text)),
                    }
                    for span in hidden
                ]
                document["hidden_removed"] = removed
        finally:
            await asyncio.to_thread(shutil.rmtree, workdir, True)
        if not text.strip():
            raise PermanentError("empty_result")
        return text

    @staticmethod
    async def _hidden_text(plain: Path, mime_type: str) -> list[HiddenSpan]:
        """Text a reader can't see (white, invisible, tiny, off-page) in PDFs (3.3)."""
        if mime_type != "application/pdf":
            return []
        from spoldzielnia_ai.files.pdf_hidden import find_hidden_text

        try:
            return await asyncio.to_thread(find_hidden_text, plain)
        except Exception as exc:  # pdfium refuses the file: Docling decides whether it's usable
            log.warning("worker.hidden_text_scan_failed", error=type(exc).__name__)
            return []

    async def _materialize(self, source: _Source, workdir: Path, plain: Path) -> None:
        assert source.object_key is not None  # noqa: S101 - checked by the caller
        assert source.attachment_dek is not None  # noqa: S101
        assert source.attachment_id is not None  # noqa: S101
        sealed = workdir / "sealed"
        try:
            with sealed.open("wb") as handle:
                await self._storage.get(source.object_key, handle)
        except Exception as exc:  # storage hiccup: worth retrying
            raise TransientError(f"storage: {type(exc).__name__}") from exc
        aad = row_aad(ATTACHMENT, source.attachment_id, source.owner_id)
        dek = source.attachment_dek

        def decrypt() -> None:
            with sealed.open("rb") as src, plain.open("wb") as dst:
                for segment in self._cipher.decrypt_stream(dek, src, aad):
                    dst.write(segment)

        try:
            await asyncio.to_thread(decrypt)
        except InvalidTag:
            raise PermanentError("ciphertext_invalid") from None

    async def _fetch_link(self, source: _Source) -> tuple[str, UUID | None]:
        if self._fetcher is None or source.url is None:
            raise PermanentError("fetcher_unavailable")
        try:
            result = await self._fetcher.fetch(source.url)
        except SsrfBlocked as exc:
            raise PermanentError(f"ssrf_blocked:{exc.reason}") from None
        except FetchFailed as exc:
            if exc.reason.startswith(_TRANSIENT_FETCH):
                raise TransientError(exc.reason) from None
            raise PermanentError(exc.reason) from None

        if result.content_type != "application/pdf":
            try:
                return to_markdown(result), None
            except FetchFailed as exc:
                raise PermanentError(exc.reason) from None

        # A linked PDF becomes an attachment of the note: the same validate → scan →
        # sanitise → encrypt path as an upload, then parsed by the media worker.
        if self._uploads is None:
            raise PermanentError("pdf_ingest_unavailable")
        filename = safe_filename(PurePosixPath(urlsplit(result.url).path).name or "document.pdf")
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        workdir = Path(tempfile.mkdtemp(prefix="link-", dir=self._tmp_dir))
        try:
            spool = workdir / "download"
            await asyncio.to_thread(spool.write_bytes, result.body)
            upload = SpooledUpload(
                path=spool,
                filename=filename,
                content_type="application/pdf",
                size=len(result.body),
                sha256=hashlib.sha256(result.body).hexdigest(),
            )
            try:
                view = await self._uploads.store(
                    source.owner_id, source.note_id, upload, keep_location=False
                )
            except UploadRejected as exc:
                if exc.code == "scanner_unavailable":
                    raise TransientError(exc.code) from None
                raise PermanentError(exc.code) from None
        finally:
            await asyncio.to_thread(shutil.rmtree, workdir, True)
        summary = (
            f"Pobrano dokument PDF z {result.url}. Zapisany jako załącznik „{view.filename}”; "
            "jego treść pojawi się po przetworzeniu."
        )
        return summary, view.id

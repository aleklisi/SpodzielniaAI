"""Sending an annotated photo to the shared atlas (chunk 9.4).

1. ``SubmissionService.request`` (member, RLS): the photo has marks with usable labels in an
   active domain, nothing else is in flight for it, then a PENDING submission is queued.
2. ``AtlasPreparer.handle`` (worker-core): decrypts the private original, makes the
   metadata-free shared copies (A3), checks for a near-duplicate already in the atlas, scrubs
   PII from the caption and descriptions (Presidio, local), screens them for prompt injection,
   asks the local VLM for privacy flags, stores the encrypted copies and writes a DRAFT
   ``atlas_image`` with its regions (author + consent on each, A4). Submission → PREPARED.
3. The member sees exactly what would be shared and either ``send``s it (explicit
   ``show_author`` and ``allow_export`` choices; photos with people need ``confirm_no_people``)
   → IN_REVIEW, or ``withdraw``s it.

A photo the experts sent back (REVISION_REQUIRED) is re-prepared into the same atlas photo,
keeping its review history.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidTag
from sqlalchemy import delete, func, select

from spoldzielnia_ai.atlas.annotations import AnnotationService
from spoldzielnia_ai.atlas.images import (
    AtlasObjects,
    StoredKeys,
    atlas_aad,
    make_shared_copies,
)
from spoldzielnia_ai.atlas.labels import LabelError, LabelService
from spoldzielnia_ai.atlas.privacy import PrivacyChecker, needs_people_confirmation
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import (
    AtlasImageStatus,
    AtlasSubmissionStatus,
    ConsentStatus,
    VisualDomainStatus,
    VisualLabelStatus,
)
from spoldzielnia_ai.db.models import (
    AnnotatedImage,
    AtlasImage,
    AtlasRegion,
    AtlasReview,
    AtlasSubmission,
    NoteAttachment,
    VisualDomain,
)
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.files.preview import hamming
from spoldzielnia_ai.files.validation import FileRejected
from spoldzielnia_ai.llm.gateway import LLMError
from spoldzielnia_ai.notebook.service import ATTACHMENT

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from pydantic import JsonValue

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.core.settings import AtlasSettings
    from spoldzielnia_ai.db.rls import RlsSession
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.files.storage import ObjectStorage
    from spoldzielnia_ai.llm.gateway import LLMGateway
    from spoldzielnia_ai.llm.injection_guard import InjectionGuard
    from spoldzielnia_ai.privacy.scrubber import PiiScrubber
    from spoldzielnia_ai.workers.jobs import AtlasJob

log = get_logger(__name__)

SUBMISSION = "atlas_submission"
IN_FLIGHT = (AtlasSubmissionStatus.PENDING, AtlasSubmissionStatus.PREPARED)
# Atlas photos that block a new submission of the same private photo.
LIVE = (AtlasImageStatus.IN_REVIEW, AtlasImageStatus.APPROVED)


class SubmissionError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class Choices:
    show_author: bool
    allow_export: bool
    confirm_no_people: bool


class SubmissionService:
    """Member-side operations, in the member's RLS session (shared tables have no RLS)."""

    def __init__(self, session: RlsSession, cipher: EnvelopeCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def get(self, submission_id: UUID, *, for_update: bool = False) -> AtlasSubmission:
        stmt = select(AtlasSubmission).where(AtlasSubmission.id == submission_id)
        row = await self._session.scalar(stmt.with_for_update() if for_update else stmt)
        if row is None:
            raise SubmissionError("submission_not_found", 404)
        return row

    async def list_for_photo(self, annotated_image_id: UUID) -> Sequence[AtlasSubmission]:
        return (
            await self._session.scalars(
                select(AtlasSubmission)
                .where(AtlasSubmission.annotated_image_id == annotated_image_id)
                .order_by(AtlasSubmission.created_at.desc())
            )
        ).all()

    async def atlas_image(self, submission: AtlasSubmission) -> AtlasImage | None:
        if submission.atlas_image_id is None:
            return None
        return await self._session.get(AtlasImage, submission.atlas_image_id)

    async def regions(self, image: AtlasImage) -> Sequence[AtlasRegion]:
        return (
            await self._session.scalars(
                select(AtlasRegion)
                .where(AtlasRegion.atlas_image_id == image.id)
                .order_by(AtlasRegion.position)
            )
        ).all()

    async def request(self, owner_id: UUID, note_id: UUID, attachment_id: UUID) -> AtlasSubmission:
        photo = await AnnotationService(self._session, self._cipher).get(note_id, attachment_id)
        if photo.version == 0 or not photo.regions:
            raise SubmissionError("nothing_to_send", 422)
        image = await self._session.scalar(
            select(AnnotatedImage)
            .where(AnnotatedImage.attachment_id == attachment_id)
            .with_for_update()
        )
        assert image is not None  # noqa: S101 - version > 0 means the row exists
        domain = await self._session.get(VisualDomain, image.domain_id)
        if domain is None or domain.status != VisualDomainStatus.ACTIVE:
            raise SubmissionError("domain_archived")
        labels = LabelService(self._session)
        for region in photo.regions:
            if region.label.status in (VisualLabelStatus.DEPRECATED, VisualLabelStatus.REJECTED):
                raise SubmissionError("label_not_usable", 422)
            try:
                await labels.usable(region.label.id, owner_id, image.domain_id)
            except LabelError as exc:
                raise SubmissionError(exc.code, exc.status) from None
        for previous in await self.list_for_photo(image.id):
            if previous.status in IN_FLIGHT:
                raise SubmissionError("submission_in_progress")
            atlas = await self.atlas_image(previous)
            if (
                previous.status == AtlasSubmissionStatus.SENT
                and atlas is not None
                and atlas.consent_status != ConsentStatus.REVOKED
                and atlas.status in LIVE
            ):
                raise SubmissionError("already_in_atlas")
        submission = AtlasSubmission(id=uuid7(), owner_id=owner_id, annotated_image_id=image.id)
        self._session.add(submission)
        await self._session.flush()
        await self._session.refresh(submission)
        return submission

    async def send(self, owner_id: UUID, submission_id: UUID, choices: Choices) -> AtlasSubmission:
        submission = await self.get(submission_id, for_update=True)
        if submission.status != AtlasSubmissionStatus.PREPARED:
            raise SubmissionError("not_prepared")
        image = await self._session.get(AtlasImage, submission.atlas_image_id, with_for_update=True)
        if image is None or image.status != AtlasImageStatus.DRAFT:
            raise SubmissionError("not_prepared")
        if needs_people_confirmation(image.privacy_flags) and not choices.confirm_no_people:
            raise SubmissionError("people_confirmation_required", 422)
        now = dt.datetime.now(dt.UTC)
        image.status = AtlasImageStatus.IN_REVIEW
        image.status_changed_at = now
        image.submitted_at = now
        image.show_author = choices.show_author
        image.allow_export = choices.allow_export
        image.review_alert = None
        submission.status = AtlasSubmissionStatus.SENT
        await self._session.flush()
        regions = await self.regions(image)
        await AuditService(self._session).record(
            EventType.ATLAS_SUBMITTED,
            owner_id,
            AuditTarget("atlas_image", str(image.id)),
            {
                "regions": len(regions),
                "version": image.version,
                "show_author": choices.show_author,
                "allow_export": choices.allow_export,
                "privacy_flags": [k for k, v in image.privacy_flags.items() if v is True],
            },
        )
        await self._session.refresh(submission)
        return submission

    async def withdraw(self, owner_id: UUID, submission_id: UUID) -> list[str]:
        """Drop a prepared (not yet sent) submission. Returns object keys to delete after
        commit. A re-prepared photo with a review history goes back to REVISION_REQUIRED."""
        submission = await self.get(submission_id, for_update=True)
        if submission.status not in IN_FLIGHT:
            raise SubmissionError("not_withdrawable")
        submission.status = AtlasSubmissionStatus.WITHDRAWN
        keys: list[str] = []
        image = await self.atlas_image(submission)
        if image is not None and image.status == AtlasImageStatus.DRAFT:
            reviewed = await self._session.scalar(
                select(func.count())
                .select_from(AtlasReview)
                .where(AtlasReview.atlas_image_id == image.id)
            )
            if reviewed:
                image.status = AtlasImageStatus.REVISION_REQUIRED
            else:
                keys = [
                    k
                    for k in (image.object_key, image.preview_object_key, image.thumb_object_key)
                    if k is not None
                ]
                submission.atlas_image_id = None
                await self._session.flush()
                await self._session.delete(image)
        await self._session.flush()
        await AuditService(self._session).record(
            EventType.ATLAS_SUBMISSION_WITHDRAWN,
            owner_id,
            AuditTarget(SUBMISSION, str(submission.id)),
            {},
        )
        return keys


# --- Worker ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Source:
    owner_id: UUID
    domain_id: UUID
    caption: str | None
    regions: list[tuple[UUID, float, float, float, float, str | None]]
    object_key: str
    attachment_id: UUID
    attachment_dek: bytes
    reuse_image_id: UUID | None


class AtlasPreparer:
    def __init__(
        self,
        factory: SessionFactory,
        cipher: EnvelopeCipher,
        storage: ObjectStorage,
        scrubber: PiiScrubber,
        guard: InjectionGuard,
        gateway: LLMGateway,
        settings: AtlasSettings,
        *,
        max_pixels: int,
        tmp_dir: str | None = None,
    ) -> None:
        self._factory = factory
        self._cipher = cipher
        self._storage = storage
        self._objects = AtlasObjects(cipher, storage)
        self._scrubber = scrubber
        self._guard = guard
        self._privacy = PrivacyChecker(gateway)
        self._settings = settings
        self._max_pixels = max_pixels
        self._tmp_dir = tmp_dir

    async def handle(self, job: AtlasJob, final_attempt: bool) -> bool:
        """Returns True if the caller should retry later."""
        source = await self._load(job)
        if source is None:
            return False
        try:
            with tempfile.TemporaryDirectory(prefix="atlas-", dir=self._tmp_dir) as workdir:
                original = Path(workdir) / "original"
                await self._decrypt(source, original)
                copies = await asyncio.to_thread(make_shared_copies, original, self._max_pixels)
        except (FileRejected, InvalidTag):
            await self._fail(job, "image_unreadable")
            return False

        if await self._is_duplicate(copies.phash, copies.sha256, source):
            await self._fail(job, "duplicate_photo")
            return False

        texts = [source.caption or "", *[(r[5] or "") for r in source.regions]]
        scrubbed = await asyncio.to_thread(self._scrubber.scrub_many, texts)
        pii: dict[str, int] = {}
        for result in scrubbed:
            for entity, count in result.stats.items():
                pii[entity] = pii.get(entity, 0) + count
        for result in scrubbed:
            if not result.text.strip():
                continue
            verdict = await asyncio.to_thread(self._guard.scan, result.text)
            if verdict.quarantine:
                await self._fail(job, "injection_detected", {"kinds": list(verdict.kinds())})
                return False

        try:
            privacy = await self._privacy.check(copies.preview.data)
        except LLMError:
            if not final_attempt:
                return True
            privacy = {"check": "unavailable"}

        image_id = source.reuse_image_id or uuid7()
        aad = atlas_aad(image_id, job.owner_id)
        if source.reuse_image_id is None:
            key = self._cipher.new_key(aad)
            dek, wrapped = key.key, key.wrapped
        else:
            wrapped = await self._wrapped(source.reuse_image_id)
            dek = self._cipher.unwrap(wrapped, aad)
        stored = await self._objects.store(copies, image_id, job.owner_id, dek)
        caption = scrubbed[0].text.strip() or None
        descriptions = [r.text.strip() or None for r in scrubbed[1:]]
        try:
            old_keys = await self._write(
                job, source, image_id, wrapped, stored, copies, caption, descriptions, privacy, pii
            )
        except BaseException:
            await self._objects.delete(stored.all())
            raise
        if old_keys is None:  # withdrawn or deleted meanwhile
            await self._objects.delete(stored.all())
        else:
            await self._objects.delete(old_keys)
        return False

    async def _load(self, job: AtlasJob) -> _Source | None:
        async with rls_session(self._factory, job.owner_id) as session:
            submission = await session.get(AtlasSubmission, job.submission_id)
            if submission is None or submission.status != AtlasSubmissionStatus.PENDING:
                return None  # redelivered, or withdrawn meanwhile
            image = await session.get(AnnotatedImage, submission.annotated_image_id)
            attachment = await session.get(NoteAttachment, image.attachment_id) if image else None
            if image is None or attachment is None:
                return None
            photo = await AnnotationService(session, self._cipher).get(
                attachment.note_id, attachment.id
            )
            reuse = await self._reusable(session, image.id)
            aad = row_aad(ATTACHMENT, attachment.id, attachment.owner_id)
            return _Source(
                owner_id=attachment.owner_id,
                domain_id=image.domain_id,
                caption=photo.caption,
                regions=[(r.label.id, r.x, r.y, r.w, r.h, r.description) for r in photo.regions],
                object_key=attachment.object_key,
                attachment_id=attachment.id,
                attachment_dek=self._cipher.unwrap(attachment.dek_wrapped, aad),
                reuse_image_id=reuse,
            )

    @staticmethod
    async def _reusable(session: RlsSession, annotated_image_id: UUID) -> UUID | None:
        """The atlas photo the experts sent back for this private photo, if any."""
        return await session.scalar(
            select(AtlasImage.id)
            .join(AtlasSubmission, AtlasSubmission.atlas_image_id == AtlasImage.id)
            .where(
                AtlasSubmission.annotated_image_id == annotated_image_id,
                AtlasImage.status.in_([AtlasImageStatus.REVISION_REQUIRED, AtlasImageStatus.DRAFT]),
                AtlasImage.consent_status != ConsentStatus.REVOKED,
            )
            .order_by(AtlasSubmission.created_at.desc())
            .limit(1)
        )

    async def _decrypt(self, source: _Source, target: Path) -> None:
        sealed = target.with_suffix(".sealed")
        with sealed.open("wb") as handle:
            await self._storage.get(source.object_key, handle)
        aad = row_aad(ATTACHMENT, source.attachment_id, source.owner_id)

        def decrypt() -> None:
            with sealed.open("rb") as src, target.open("wb") as dst:
                for segment in self._cipher.decrypt_stream(source.attachment_dek, src, aad):
                    dst.write(segment)

        await asyncio.to_thread(decrypt)

    async def _is_duplicate(self, phash: str, sha256: str, source: _Source) -> bool:
        async with self._factory() as session:
            rows = await session.execute(
                select(AtlasImage.id, AtlasImage.phash, AtlasImage.sha256).where(
                    AtlasImage.domain_id == source.domain_id,
                    AtlasImage.consent_status != ConsentStatus.REVOKED,
                    AtlasImage.status != AtlasImageStatus.REJECTED,
                )
            )
            limit = self._settings.duplicate_max_distance
            for image_id, other_phash, other_sha in rows:
                if image_id == source.reuse_image_id:
                    continue
                if other_sha == sha256 or (other_phash and hamming(phash, other_phash) <= limit):
                    return True
        return False

    async def _wrapped(self, image_id: UUID) -> bytes:
        async with self._factory() as session:
            wrapped = await session.scalar(
                select(AtlasImage.dek_wrapped).where(AtlasImage.id == image_id)
            )
        if wrapped is None:
            raise LookupError("atlas image gone")
        return wrapped

    async def _write(
        self,
        job: AtlasJob,
        source: _Source,
        image_id: UUID,
        wrapped: bytes,
        stored: StoredKeys,
        copies: Any,
        caption: str | None,
        descriptions: list[str | None],
        privacy: dict[str, Any],
        pii: dict[str, int],
    ) -> list[str] | None:
        """One transaction (the member's RLS session; shared tables have no RLS). Returns the
        replaced object keys of a re-prepared photo, or None if the submission is gone."""
        async with rls_session(self._factory, job.owner_id) as session:
            submission = await session.get(AtlasSubmission, job.submission_id, with_for_update=True)
            if submission is None or submission.status != AtlasSubmissionStatus.PENDING:
                return None
            labels = await LabelService(session).resolve(r[0] for r in source.regions)
            old_keys: list[str] = []
            image = await session.get(AtlasImage, image_id, with_for_update=True)
            if image is None:
                image = AtlasImage(
                    id=image_id,
                    author_id=job.owner_id,
                    domain_id=source.domain_id,
                    dek_wrapped=wrapped,
                    width=copies.full.out_width,
                    height=copies.full.out_height,
                )
                session.add(image)
            else:
                old_keys = [
                    k
                    for k in (image.object_key, image.preview_object_key, image.thumb_object_key)
                    if k is not None
                ]
                image.version += 1
                image.domain_id = source.domain_id
                image.width, image.height = copies.full.out_width, copies.full.out_height
                await session.execute(
                    delete(AtlasRegion).where(AtlasRegion.atlas_image_id == image.id)
                )
            image.status = AtlasImageStatus.DRAFT
            image.status_changed_at = dt.datetime.now(dt.UTC)
            image.object_key = stored.full
            image.preview_object_key = stored.preview
            image.thumb_object_key = stored.thumb
            image.sha256 = copies.sha256
            image.phash = copies.phash
            image.caption = caption
            image.privacy_flags = privacy
            await session.flush()
            for position, (region, description) in enumerate(
                zip(source.regions, descriptions, strict=True)
            ):
                label_id, x, y, w, h, _ = region
                session.add(
                    AtlasRegion(
                        atlas_image_id=image.id,
                        author_id=job.owner_id,
                        label_id=labels[label_id].id if label_id in labels else label_id,
                        x=x,
                        y=y,
                        w=w,
                        h=h,
                        description=description,
                        position=position,
                    )
                )
            submission.status = AtlasSubmissionStatus.PREPARED
            submission.atlas_image_id = image.id
            submission.pii_stats = dict(pii)
            submission.error = None
        log.info("atlas.prepared", submission=str(job.submission_id), regions=len(source.regions))
        return old_keys

    async def _fail(
        self, job: AtlasJob, code: str, details: dict[str, JsonValue] | None = None
    ) -> None:
        async with rls_session(self._factory, job.owner_id) as session:
            submission = await session.get(AtlasSubmission, job.submission_id, with_for_update=True)
            if submission is None or submission.status != AtlasSubmissionStatus.PENDING:
                return
            submission.status = AtlasSubmissionStatus.FAILED
            submission.error = code[:64]
            event = (
                EventType.PROMPT_INJECTION_DETECTED
                if code == "injection_detected"
                else EventType.ATLAS_SUBMISSION_FAILED
            )
            await AuditService(session).record(
                event,
                job.owner_id,
                AuditTarget(SUBMISSION, str(job.submission_id)),
                {"error": code, **(details or {})},
            )
        log.info("atlas.failed", submission=str(job.submission_id), error=code)

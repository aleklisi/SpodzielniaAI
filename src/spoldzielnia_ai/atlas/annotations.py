"""A member's annotated photos in the private notebook (chunk 9.2).

Everything runs in the owner's ``RlsSession``: another member's photo is indistinguishable from
a missing one. Caption and mark descriptions are ciphertext under the ``annotated_image`` row's
DEK. A save replaces the full set of marks (the editor always sends all of them) and checks the
version the editor loaded (optimistic locking, 409 ``stale_version``).

After a save the caption and marks are mirrored into one ``ANNOTATIONS`` derivative of the
attachment, which goes through the usual injection screening and private indexing, so "ask my
notebook" finds annotated photos. An emptied photo drops the derivative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from spoldzielnia_ai.atlas.labels import LabelError, LabelService
from spoldzielnia_ai.core.crypto import row_aad
from spoldzielnia_ai.core.ids import uuid7
from spoldzielnia_ai.db.enums import (
    AnnotationSource,
    DerivativeKind,
    DerivativeStatus,
    VisualDomainStatus,
    VisualLabelStatus,
)
from spoldzielnia_ai.db.models import (
    AnnotatedImage,
    ImageAnnotation,
    NoteAttachment,
    NoteDerivative,
    VisualDomain,
)
from spoldzielnia_ai.db.models.atlas import MIN_BOX_SIDE
from spoldzielnia_ai.notebook.images import IMAGE_TYPES
from spoldzielnia_ai.notebook.service import DERIVATIVE

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from spoldzielnia_ai.core.crypto import EnvelopeCipher
    from spoldzielnia_ai.db.models import VisualLabel
    from spoldzielnia_ai.db.rls import RlsSession

ANNOTATED_IMAGE = "annotated_image"
MAX_REGIONS = 50
MAX_CAPTION = 2000
MAX_DESCRIPTION = 1000
# Coordinates come from floating-point pointer maths in the browser.
EPSILON = 1e-6


class AnnotationError(Exception):
    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class RegionIn:
    label_id: UUID
    x: float
    y: float
    w: float
    h: float
    description: str | None = None
    source: AnnotationSource = AnnotationSource.USER


@dataclass(frozen=True, slots=True)
class LabelRef:
    id: UUID
    name: str
    status: VisualLabelStatus


@dataclass(frozen=True, slots=True)
class RegionView:
    id: UUID
    label: LabelRef
    x: float
    y: float
    w: float
    h: float
    description: str | None
    source: AnnotationSource


@dataclass(frozen=True, slots=True)
class PhotoView:
    attachment_id: UUID
    note_id: UUID
    mime_type: str
    width: int | None
    height: int | None
    domain_id: UUID | None
    caption: str | None
    version: int  # 0: never annotated
    regions: Sequence[RegionView]
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class SaveResult:
    photo: PhotoView
    derivative_id: UUID | None  # to screen and index (None: nothing to index)
    dropped_derivative_id: UUID | None  # whose vectors to delete


def _clean(value: str | None, limit: int, code: str) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > limit:
        raise AnnotationError(code)
    return value or None


def _validate_box(region: RegionIn) -> RegionIn:
    values = (region.x, region.y, region.w, region.h)
    if any(v != v or v in (float("inf"), float("-inf")) for v in values):  # NaN, infinity
        raise AnnotationError("invalid_box")
    x, y, w, h = values
    if x < -EPSILON or y < -EPSILON or w < MIN_BOX_SIDE or h < MIN_BOX_SIDE:
        raise AnnotationError("invalid_box")
    if x + w > 1 + EPSILON or y + h > 1 + EPSILON:
        raise AnnotationError("invalid_box")
    # Snap rounding noise back inside the image (the DB CHECK has the same tolerance).
    x, y = max(x, 0.0), max(y, 0.0)
    return RegionIn(
        region.label_id, x, y, min(w, 1 - x), min(h, 1 - y), region.description, region.source
    )


def annotations_text(domain: str, caption: str | None, regions: Sequence[RegionView]) -> str:
    """The derivative text: what the member wrote, in their words, plus the label names."""
    lines = [f"Zdjęcie ({domain})" + (f": {caption}" if caption else "")]
    if regions:
        lines.append("Oznaczenia:")
        lines.extend(
            f"- {r.label.name}" + (f": {r.description}" if r.description else "") for r in regions
        )
    return "\n".join(lines)


class AnnotationService:
    def __init__(self, session: RlsSession, cipher: EnvelopeCipher) -> None:
        self._session = session
        self._cipher = cipher
        self._labels = LabelService(session)

    async def _attachment(self, note_id: UUID, attachment_id: UUID) -> NoteAttachment:
        row = await self._session.scalar(
            select(NoteAttachment).where(
                NoteAttachment.id == attachment_id,
                NoteAttachment.note_id == note_id,
                NoteAttachment.mime_type.in_(IMAGE_TYPES),
            )
        )
        if row is None:
            raise AnnotationError("photo_not_found", 404)
        return row

    async def _image(
        self, attachment_id: UUID, *, for_update: bool = False
    ) -> AnnotatedImage | None:
        stmt = select(AnnotatedImage).where(AnnotatedImage.attachment_id == attachment_id)
        return await self._session.scalar(stmt.with_for_update() if for_update else stmt)

    def _dek(self, image: AnnotatedImage) -> tuple[bytes, str]:
        aad = row_aad(ANNOTATED_IMAGE, image.id, image.owner_id)
        return self._cipher.unwrap(image.dek_wrapped, aad), aad

    async def _view(self, attachment: NoteAttachment, image: AnnotatedImage | None) -> PhotoView:
        if image is None:
            return PhotoView(
                attachment.id,
                attachment.note_id,
                attachment.mime_type,
                attachment.width,
                attachment.height,
                None,
                None,
                0,
                [],
                None,
            )
        dek, aad = self._dek(image)
        rows = (
            await self._session.scalars(
                select(ImageAnnotation)
                .where(ImageAnnotation.annotated_image_id == image.id)
                .order_by(ImageAnnotation.position)
            )
        ).all()
        labels = await self._labels.resolve({r.label_id for r in rows})
        regions = [
            RegionView(
                id=r.id,
                label=_ref(labels.get(r.label_id)),
                x=r.x,
                y=r.y,
                w=r.w,
                h=r.h,
                description=(
                    self._cipher.decrypt_field(
                        dek, r.description_ciphertext, aad, f"description:{r.id}"
                    ).decode()
                    if r.description_ciphertext is not None
                    else None
                ),
                source=r.source,
            )
            for r in rows
        ]
        caption = (
            self._cipher.decrypt_field(dek, image.caption_ciphertext, aad, "caption").decode()
            if image.caption_ciphertext is not None
            else None
        )
        return PhotoView(
            attachment.id,
            attachment.note_id,
            attachment.mime_type,
            attachment.width,
            attachment.height,
            image.domain_id,
            caption,
            image.version,
            regions,
            image.updated_at,
        )

    async def get(self, note_id: UUID, attachment_id: UUID) -> PhotoView:
        attachment = await self._attachment(note_id, attachment_id)
        return await self._view(attachment, await self._image(attachment.id))

    async def _check_labels(
        self, owner_id: UUID, domain_id: UUID, regions: Sequence[RegionIn], kept: set[UUID]
    ) -> None:
        """Each label must be usable in the domain. A label already on this photo stays
        allowed even after experts deprecate it: saving a caption must not force relabelling."""
        for label_id in {r.label_id for r in regions} - kept:
            try:
                await self._labels.usable(label_id, owner_id, domain_id)
            except LabelError as exc:
                raise AnnotationError(exc.code, exc.status) from None

    async def save(
        self,
        owner_id: UUID,
        note_id: UUID,
        attachment_id: UUID,
        *,
        domain_id: UUID,
        caption: str | None,
        regions: Sequence[RegionIn],
        expected_version: int,
    ) -> SaveResult:
        attachment = await self._attachment(note_id, attachment_id)
        if len(regions) > MAX_REGIONS:
            raise AnnotationError("too_many_regions")
        caption = _clean(caption, MAX_CAPTION, "caption_too_long")
        clean_regions = [_validate_box(r) for r in regions]
        descriptions = [
            _clean(r.description, MAX_DESCRIPTION, "description_too_long") for r in clean_regions
        ]

        image = await self._image(attachment.id, for_update=True)
        if (image.version if image else 0) != expected_version:
            raise AnnotationError("stale_version", 409)
        domain = await self._session.get(VisualDomain, domain_id)
        if domain is None:
            raise AnnotationError("domain_not_found", 404)
        # An archived domain keeps its photos read-only and takes no new ones.
        if domain.status != VisualDomainStatus.ACTIVE:
            raise AnnotationError("domain_archived", 409)
        kept: set[UUID] = set()
        if image is not None and image.domain_id == domain_id:
            kept = set(
                (
                    await self._session.scalars(
                        select(ImageAnnotation.label_id).where(
                            ImageAnnotation.annotated_image_id == image.id
                        )
                    )
                ).all()
            )
        await self._check_labels(owner_id, domain_id, clean_regions, kept)

        if image is None:
            image_id = uuid7()
            aad = row_aad(ANNOTATED_IMAGE, image_id, owner_id)
            key = self._cipher.new_key(aad)
            image = AnnotatedImage(
                id=image_id,
                owner_id=owner_id,
                note_id=attachment.note_id,
                attachment_id=attachment.id,
                domain_id=domain_id,
                dek_wrapped=key.wrapped,
                version=1,
            )
            self._session.add(image)
            dek = key.key
        else:
            dek, aad = self._dek(image)
            image.domain_id = domain_id
            image.version += 1
            await self._session.execute(
                delete(ImageAnnotation).where(ImageAnnotation.annotated_image_id == image.id)
            )
        image.caption_ciphertext = (
            self._cipher.encrypt_field(dek, caption.encode(), aad, "caption") if caption else None
        )
        await self._session.flush()
        for position, (region, description) in enumerate(
            zip(clean_regions, descriptions, strict=True)
        ):
            region_id = uuid7()
            self._session.add(
                ImageAnnotation(
                    id=region_id,
                    owner_id=owner_id,
                    annotated_image_id=image.id,
                    label_id=region.label_id,
                    x=region.x,
                    y=region.y,
                    w=region.w,
                    h=region.h,
                    description_ciphertext=(
                        self._cipher.encrypt_field(
                            dek, description.encode(), aad, f"description:{region_id}"
                        )
                        if description
                        else None
                    ),
                    source=region.source,
                    position=position,
                )
            )
        await self._session.flush()
        await self._session.refresh(image)
        view = await self._view(attachment, image)
        derivative_id, dropped = await self._mirror(attachment, domain.name, view)
        return SaveResult(view, derivative_id, dropped)

    async def _mirror(
        self, attachment: NoteAttachment, domain: str, view: PhotoView
    ) -> tuple[UUID | None, UUID | None]:
        """Write (or drop) the attachment's ANNOTATIONS derivative. It restarts as DONE with no
        scan result, so worker-core screens it again before it is (re)indexed."""
        row = await self._session.scalar(
            select(NoteDerivative)
            .where(
                NoteDerivative.attachment_id == attachment.id,
                NoteDerivative.kind == DerivativeKind.ANNOTATIONS,
            )
            .with_for_update()
        )
        if not view.caption and not view.regions:
            if row is None:
                return None, None
            await self._session.delete(row)
            await self._session.flush()
            return None, row.id
        text = annotations_text(domain, view.caption, view.regions)
        if row is None:
            derivative_id = uuid7()
            aad = row_aad(DERIVATIVE, derivative_id, attachment.owner_id)
            key = self._cipher.new_key(aad)
            row = NoteDerivative(
                id=derivative_id,
                owner_id=attachment.owner_id,
                note_id=attachment.note_id,
                attachment_id=attachment.id,
                kind=DerivativeKind.ANNOTATIONS,
                source_ciphertext=None,
                dek_wrapped=key.wrapped,
            )
            self._session.add(row)
            dek = key.key
        else:
            aad = row_aad(DERIVATIVE, row.id, row.owner_id)
            dek = self._cipher.unwrap(row.dek_wrapped, aad)
        row.content_ciphertext = self._cipher.encrypt_field(dek, text.encode(), aad, "content")
        row.status = DerivativeStatus.DONE
        row.error = None
        row.injection_flags = {}
        row.quarantine_confirmed_at = None
        await self._session.flush()
        return row.id, None


def _ref(label: VisualLabel | None) -> LabelRef:
    if label is None:  # labels are never deleted; a dangling id would be a bug
        raise AnnotationError("label_not_found", 500)
    return LabelRef(label.id, label.name, label.status)

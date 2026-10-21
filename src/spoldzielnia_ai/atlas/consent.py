"""Exit Strategy for atlas photos (chunk 9.6, A6): the author suspends, resumes or revokes.

The unit of consent is the whole photo: the image is one author's work, so marks an expert
added to it follow it. Like wiki blocks (4.3), the scope is built into the query (another
author's photo matches nothing) and every change is audited.

- SUSPEND: ACTIVE → SUSPENDED. Hidden from the atlas, recognition and exports; reversible.
- RESUME: SUSPENDED → ACTIVE. Visible again only if still APPROVED (the review state is kept).
- REVOKE: ACTIVE/SUSPENDED → REVOKED, final. Objects deleted (keys returned for after commit),
  caption and descriptions wiped (tombstone CHECKs), regions REVOKED. An export already handed
  out cannot be recalled; its manifest shows the photo as revoked.

Two further author choices, independent of consent: ``show_author`` (A12) and
``allow_export`` (A9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from spoldzielnia_ai.atlas.vectors import AtlasOp, delete_image, resume_image, suspend_image
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.db.enums import AtlasImageStatus, ConsentStatus
from spoldzielnia_ai.db.models import AtlasImage, AtlasRegion

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

TARGET = "atlas_image"


class PhotoConsentAction(StrEnum):
    SUSPEND = "SUSPEND"
    RESUME = "RESUME"
    REVOKE = "REVOKE"


SOURCE: dict[PhotoConsentAction, frozenset[ConsentStatus]] = {
    PhotoConsentAction.SUSPEND: frozenset({ConsentStatus.ACTIVE}),
    PhotoConsentAction.RESUME: frozenset({ConsentStatus.SUSPENDED}),
    PhotoConsentAction.REVOKE: frozenset({ConsentStatus.ACTIVE, ConsentStatus.SUSPENDED}),
}
NEW_STATUS: dict[PhotoConsentAction, ConsentStatus] = {
    PhotoConsentAction.SUSPEND: ConsentStatus.SUSPENDED,
    PhotoConsentAction.RESUME: ConsentStatus.ACTIVE,
    PhotoConsentAction.REVOKE: ConsentStatus.REVOKED,
}
EVENT: dict[PhotoConsentAction, EventType] = {
    PhotoConsentAction.SUSPEND: EventType.CONSENT_SUSPENDED,
    PhotoConsentAction.RESUME: EventType.CONSENT_RESUMED,
    PhotoConsentAction.REVOKE: EventType.CONSENT_REVOKED,
}


class PhotoConsentError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class PhotoConsentResult:
    action: PhotoConsentAction
    image: AtlasImage
    object_keys: list[str] = field(default_factory=list[str])  # delete after commit (revoke)
    ops: list[AtlasOp] = field(default_factory=lambda: list[AtlasOp]())  # visual index (9.8)


class AtlasConsentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    async def mine(self, author_id: UUID) -> list[AtlasImage]:
        return list(
            await self._session.scalars(
                select(AtlasImage)
                .where(AtlasImage.author_id == author_id)
                .order_by(AtlasImage.created_at.desc())
            )
        )

    async def _own(self, author_id: UUID, image_id: UUID) -> AtlasImage:
        image = await self._session.scalar(
            select(AtlasImage)
            .where(AtlasImage.id == image_id, AtlasImage.author_id == author_id)
            .with_for_update()
        )
        if image is None:
            raise PhotoConsentError("photo_not_found", 404)
        return image

    async def change(
        self, action: PhotoConsentAction, author_id: UUID, image_id: UUID
    ) -> PhotoConsentResult:
        image = await self._own(author_id, image_id)
        if image.consent_status not in SOURCE[action]:
            raise PhotoConsentError("consent_unchanged")
        status = NEW_STATUS[action]
        image.consent_status = status
        keys: list[str] = []
        values: dict[str, object] = {"consent_status": status}
        if action == PhotoConsentAction.REVOKE:
            keys = [
                k
                for k in (image.object_key, image.preview_object_key, image.thumb_object_key)
                if k is not None
            ]
            image.object_key = image.preview_object_key = image.thumb_object_key = None
            image.caption = None
            image.allow_export = False
            values["description"] = None
        await self._session.execute(
            update(AtlasRegion).where(AtlasRegion.atlas_image_id == image.id).values(values)
        )
        await self._session.flush()
        await self._audit.record(
            EVENT[action],
            author_id,
            AuditTarget(TARGET, str(image.id)),
            {"kind": "atlas_image", "status": image.status.value},
        )
        match action:
            case PhotoConsentAction.SUSPEND:
                ops = [suspend_image(image.id)]
            case PhotoConsentAction.RESUME:
                ops = [resume_image(image.id, approved=image.status == AtlasImageStatus.APPROVED)]
            case PhotoConsentAction.REVOKE:
                ops = [delete_image(image.id)]
        return PhotoConsentResult(action, image, keys, ops)

    async def preferences(
        self,
        author_id: UUID,
        image_id: UUID,
        *,
        show_author: bool | None = None,
        allow_export: bool | None = None,
    ) -> AtlasImage:
        image = await self._own(author_id, image_id)
        if image.consent_status == ConsentStatus.REVOKED:
            raise PhotoConsentError("photo_revoked")
        target = AuditTarget(TARGET, str(image.id))
        if show_author is not None and show_author != image.show_author:
            image.show_author = show_author
            await self._audit.record(
                EventType.ATLAS_ATTRIBUTION_CHANGED, author_id, target, {"show_author": show_author}
            )
        if allow_export is not None and allow_export != image.allow_export:
            image.allow_export = allow_export
            await self._audit.record(
                EventType.ATLAS_EXPORT_CONSENT_CHANGED,
                author_id,
                target,
                {"allow_export": allow_export},
            )
        await self._session.flush()
        return image

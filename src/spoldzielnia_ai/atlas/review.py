"""Expert review of atlas photos (chunk 9.5).

- The queue holds IN_REVIEW photos with ACTIVE consent. Authors are never shown to reviewers.
- Experts may correct marks (box, label, description), add marks (authored by the expert) and
  remove marks. A corrected mark keeps its author and records ``corrected_by``. Every change
  bumps ``version``, so approvals given to an earlier version no longer count.
- Decisions: APPROVE (needs no PROPOSED label on the photo; ``review_quorum`` distinct experts
  on the same version make it APPROVED), REQUEST_CHANGES and REJECT (both need a comment).
  Nobody reviews their own photo. Optimistic locking on ``version`` (409 ``stale_version``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from spoldzielnia_ai.atlas.labels import LabelService
from spoldzielnia_ai.atlas.vectors import AtlasOp, index_images
from spoldzielnia_ai.audit.events import EventType
from spoldzielnia_ai.audit.service import AuditService, AuditTarget
from spoldzielnia_ai.db.enums import (
    AtlasImageStatus,
    ConsentStatus,
    ReviewDecision,
    VisualLabelStatus,
)
from spoldzielnia_ai.db.models import AtlasImage, AtlasRegion, AtlasReview, User, VisualDomain
from spoldzielnia_ai.db.models.atlas import MIN_BOX_SIDE

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from spoldzielnia_ai.db.models import VisualLabel

TARGET = "atlas_image"
MAX_REGIONS = 50
EPSILON = 1e-6


class ReviewError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class RegionEdit:
    id: UUID | None  # None: a new mark by the expert
    label_id: UUID
    x: float
    y: float
    w: float
    h: float
    description: str | None


@dataclass(frozen=True, slots=True)
class Outcome:
    previous: AtlasImageStatus
    status: AtlasImageStatus
    version: int
    approvals: int
    quorum: int
    ops: list[AtlasOp] = field(default_factory=lambda: list[AtlasOp]())  # visual index (9.8)


def _box_ok(e: RegionEdit) -> bool:
    values = (e.x, e.y, e.w, e.h)
    if any(v != v for v in values):
        return False
    return (
        e.x >= -EPSILON
        and e.y >= -EPSILON
        and e.w >= MIN_BOX_SIDE
        and e.h >= MIN_BOX_SIDE
        and e.x + e.w <= 1 + EPSILON
        and e.y + e.h <= 1 + EPSILON
    )


class AtlasReviewService:
    def __init__(self, session: AsyncSession, quorum: int) -> None:
        self._session = session
        self._quorum = quorum
        self._audit = AuditService(session)

    @property
    def quorum(self) -> int:
        return self._quorum

    async def queue(self) -> Sequence[AtlasImage]:
        return (
            await self._session.scalars(
                select(AtlasImage)
                .where(
                    AtlasImage.status == AtlasImageStatus.IN_REVIEW,
                    AtlasImage.consent_status == ConsentStatus.ACTIVE,
                )
                .order_by(AtlasImage.status_changed_at)
            )
        ).all()

    async def image(self, image_id: UUID, *, for_update: bool = False) -> AtlasImage:
        stmt = select(AtlasImage).where(AtlasImage.id == image_id)
        image = await self._session.scalar(stmt.with_for_update() if for_update else stmt)
        if image is None or image.consent_status == ConsentStatus.REVOKED:
            raise ReviewError("photo_not_found", 404)
        return image

    async def regions(self, image_id: UUID) -> Sequence[AtlasRegion]:
        return (
            await self._session.scalars(
                select(AtlasRegion)
                .where(AtlasRegion.atlas_image_id == image_id)
                .order_by(AtlasRegion.position)
            )
        ).all()

    async def labels(self, regions: Sequence[AtlasRegion]) -> dict[UUID, VisualLabel]:
        return await LabelService(self._session).resolve({r.label_id for r in regions})

    async def approvals(self, image: AtlasImage) -> int:
        count = await self._session.scalar(
            select(func.count(AtlasReview.reviewer_id.distinct())).where(
                AtlasReview.atlas_image_id == image.id,
                AtlasReview.image_version == image.version,
                AtlasReview.decision == ReviewDecision.APPROVE,
            )
        )
        return int(count or 0)

    async def approvals_by_image(self, images: Sequence[AtlasImage]) -> dict[UUID, int]:
        if not images:
            return {}
        rows = await self._session.execute(
            select(AtlasReview.atlas_image_id, AtlasReview.image_version, AtlasReview.reviewer_id)
            .where(
                AtlasReview.atlas_image_id.in_([i.id for i in images]),
                AtlasReview.decision == ReviewDecision.APPROVE,
            )
            .distinct()
        )
        versions = {i.id: i.version for i in images}
        counts: dict[UUID, int] = dict.fromkeys(versions, 0)
        for image_id, version, _ in rows:
            if versions.get(image_id) == version:
                counts[image_id] += 1
        return counts

    async def history(self, image_id: UUID) -> list[tuple[AtlasReview, str | None]]:
        rows = await self._session.execute(
            select(AtlasReview, User.display_name, User.username)
            .join(User, User.id == AtlasReview.reviewer_id)
            .where(AtlasReview.atlas_image_id == image_id)
            .order_by(AtlasReview.created_at)
        )
        return [(review, display or username) for review, display, username in rows]

    async def domain_name(self, domain_id: UUID) -> str:
        domain = await self._session.get(VisualDomain, domain_id)
        return domain.name if domain else ""

    def _reviewable(self, image: AtlasImage, version: int) -> None:
        if (
            image.status != AtlasImageStatus.IN_REVIEW
            or image.consent_status != ConsentStatus.ACTIVE
        ):
            raise ReviewError("not_in_review")
        if image.version != version:
            raise ReviewError("stale_version")

    async def correct(
        self, expert_id: UUID, image_id: UUID, version: int, edits: Sequence[RegionEdit]
    ) -> AtlasImage:
        image = await self.image(image_id, for_update=True)
        self._reviewable(image, version)
        if image.author_id == expert_id:
            raise ReviewError("own_photo", 403)
        if len(edits) > MAX_REGIONS:
            raise ReviewError("too_many_regions", 422)
        if not all(_box_ok(e) for e in edits):
            raise ReviewError("invalid_box", 422)
        existing = {r.id: r for r in await self.regions(image.id)}
        if any(e.id is not None and e.id not in existing for e in edits):
            raise ReviewError("region_not_found", 404)
        labels = LabelService(self._session)
        resolved = await labels.resolve({e.label_id for e in edits})
        for edit in edits:
            label = resolved.get(edit.label_id)
            unchanged = edit.id is not None and existing[edit.id].label_id == edit.label_id
            if label is None or label.domain_id != image.domain_id:
                raise ReviewError("label_not_found", 404)
            if label.status != VisualLabelStatus.ACTIVE and not unchanged:
                raise ReviewError("label_not_usable", 422)

        kept = {e.id for e in edits if e.id is not None}
        removed = [r for rid, r in existing.items() if rid not in kept]
        for region in removed:
            await self._session.delete(region)
        await self._session.flush()
        # Positions are unique per photo: park the kept rows out of the way, then renumber.
        await self._session.execute(
            update(AtlasRegion)
            .where(AtlasRegion.atlas_image_id == image.id)
            .values(position=AtlasRegion.position + 10_000)
        )
        await self._session.flush()
        changed = added = 0
        for position, edit in enumerate(edits):
            label_id = resolved[edit.label_id].id
            description = (edit.description or "").strip() or None
            if edit.id is None:
                self._session.add(
                    AtlasRegion(
                        atlas_image_id=image.id,
                        author_id=expert_id,
                        label_id=label_id,
                        x=edit.x,
                        y=edit.y,
                        w=edit.w,
                        h=edit.h,
                        description=description,
                        position=position,
                    )
                )
                added += 1
                continue
            region = existing[edit.id]
            before = (region.label_id, region.x, region.y, region.w, region.h, region.description)
            after = (label_id, edit.x, edit.y, edit.w, edit.h, description)
            if before != after:
                region.label_id, region.x, region.y, region.w, region.h, region.description = after
                region.corrected_by = expert_id
                changed += 1
            region.position = position
        image.version += 1
        await self._session.flush()
        await self._audit.record(
            EventType.ATLAS_CORRECTED,
            expert_id,
            AuditTarget(TARGET, str(image.id)),
            {"changed": changed, "added": added, "removed": len(removed), "version": image.version},
        )
        return image

    async def decide(
        self,
        expert_id: UUID,
        image_id: UUID,
        decision: ReviewDecision,
        comment: str | None,
        version: int,
    ) -> Outcome:
        image = await self.image(image_id, for_update=True)
        self._reviewable(image, version)
        if image.author_id == expert_id:
            raise ReviewError("own_photo", 403)
        comment = (comment or "").strip() or None
        previous = image.status
        if decision == ReviewDecision.APPROVE:
            regions = await self.regions(image.id)
            labels = await self.labels(regions)
            if any(labels[r.label_id].status == VisualLabelStatus.PROPOSED for r in regions):
                raise ReviewError("proposed_labels_pending")
            already = await self._session.scalar(
                select(AtlasReview.id).where(
                    AtlasReview.atlas_image_id == image.id,
                    AtlasReview.image_version == image.version,
                    AtlasReview.reviewer_id == expert_id,
                    AtlasReview.decision == ReviewDecision.APPROVE,
                )
            )
            if already is not None:
                raise ReviewError("already_approved")
        elif decision in (ReviewDecision.REQUEST_CHANGES, ReviewDecision.REJECT):
            if comment is None:
                raise ReviewError("comment_required", 422)
        else:
            raise ReviewError("decision_not_allowed", 422)

        self._session.add(
            AtlasReview(
                atlas_image_id=image.id,
                reviewer_id=expert_id,
                decision=decision,
                comment=comment,
                image_version=image.version,
            )
        )
        await self._session.flush()
        approvals = await self.approvals(image)
        now = dt.datetime.now(dt.UTC)
        if decision == ReviewDecision.APPROVE and approvals >= self._quorum:
            image.status = AtlasImageStatus.APPROVED
            image.verified_at = now
            image.review_alert = None
        elif decision == ReviewDecision.REQUEST_CHANGES:
            image.status = AtlasImageStatus.REVISION_REQUIRED
            image.review_alert = comment
        elif decision == ReviewDecision.REJECT:
            image.status = AtlasImageStatus.REJECTED
            image.review_alert = comment
        target = AuditTarget(TARGET, str(image.id))
        await self._audit.record(
            EventType.ATLAS_REVIEWED,
            expert_id,
            target,
            {
                "decision": decision.value,
                "version": image.version,
                "approvals": approvals,
                "quorum": self._quorum,
            },
        )
        if image.status != previous:
            image.status_changed_at = now
            await self._audit.record(
                EventType.ATLAS_STATUS_CHANGED,
                expert_id,
                target,
                {"from": previous.value, "to": image.status.value},
            )
        await self._session.flush()
        ops = [index_images([image.id])] if image.status == AtlasImageStatus.APPROVED else []
        return Outcome(previous, image.status, image.version, approvals, self._quorum, ops)


# --- Members' view --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GalleryPage:
    items: Sequence[AtlasImage]
    total: int


class AtlasGallery:
    """Approved photos with ACTIVE consent, by domain, label or text."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        domain_id: UUID | None,
        label_id: UUID | None,
        q: str,
        offset: int,
        limit: int,
    ) -> GalleryPage:
        from spoldzielnia_ai.atlas.text import fold

        stmt = select(AtlasImage).where(
            AtlasImage.status == AtlasImageStatus.APPROVED,
            AtlasImage.consent_status == ConsentStatus.ACTIVE,
        )
        if domain_id is not None:
            stmt = stmt.where(AtlasImage.domain_id == domain_id)
        if label_id is not None:
            resolved = await LabelService(self._session).resolve([label_id])
            target = resolved[label_id].id if label_id in resolved else label_id
            stmt = stmt.where(
                AtlasImage.id.in_(
                    select(AtlasRegion.atlas_image_id).where(AtlasRegion.label_id == target)
                )
            )
        if fold(q):
            from spoldzielnia_ai.db.models import VisualLabel as Label

            pattern = (
                "%" + fold(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            by_label = (
                select(AtlasRegion.atlas_image_id)
                .join(Label, Label.id == AtlasRegion.label_id)
                .where(Label.search_key.ilike(pattern, escape="\\"))
            )
            stmt = stmt.where(
                AtlasImage.id.in_(by_label) | AtlasImage.caption.ilike(f"%{q.strip()}%")
            )
        total = await self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        items = (
            await self._session.scalars(
                stmt.order_by(AtlasImage.verified_at.desc(), AtlasImage.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        return GalleryPage(items, int(total or 0))

    async def label_names(self, image_ids: Sequence[UUID]) -> dict[UUID, list[str]]:
        if not image_ids:
            return {}
        rows = await self._session.execute(
            select(AtlasRegion.atlas_image_id, AtlasRegion.label_id).where(
                AtlasRegion.atlas_image_id.in_(image_ids)
            )
        )
        pairs = list(rows)
        labels = await LabelService(self._session).resolve({label for _, label in pairs})
        names: dict[UUID, list[str]] = {i: [] for i in image_ids}
        for image_id, label_id in pairs:
            name = labels[label_id].name if label_id in labels else "?"
            if name not in names[image_id]:
                names[image_id].append(name)
        return names

    async def authors(self, images: Sequence[AtlasImage]) -> dict[UUID, str | None]:
        """Display names only for photos whose author chose to be named (A12)."""
        ids = {i.author_id for i in images if i.show_author}
        names = await LabelService(self._session).user_names(ids)
        return {i.id: names.get(i.author_id) if i.show_author else None for i in images}

"""``/atlas/review``: the Expert Council's atlas queue, corrections and decisions (9.5).
EXPERT role + MFA. Reviewers never see who sent a photo."""

from __future__ import annotations

import datetime as dt  # noqa: TC003 - pydantic resolves annotations at runtime
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from spoldzielnia_ai.atlas.coverage import invalidate_corpus
from spoldzielnia_ai.atlas.privacy import flagged
from spoldzielnia_ai.atlas.review import AtlasReviewService, RegionEdit, ReviewError
from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.db.enums import (
    AtlasImageStatus,
    ReviewDecision,
    Role,
    VisualLabelStatus,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.models import AtlasImage
    from spoldzielnia_ai.db.session import SessionFactory

router = elevated_router(Role.EXPERT, prefix="/atlas/review", tags=["atlas"])

Unit = Annotated[float, Field(ge=0, le=1)]


class QueuePhotoOut(BaseModel):
    id: UUID
    domain_id: UUID
    version: int
    regions: int
    proposed_labels: int
    privacy_flags: list[str]
    privacy_check: str
    approvals: int
    quorum: int
    is_own: bool
    submitted_at: dt.datetime | None


class ReviewLabelOut(BaseModel):
    id: UUID
    name: str
    status: VisualLabelStatus


class ReviewRegionOut(BaseModel):
    id: UUID
    label: ReviewLabelOut
    x: float
    y: float
    w: float
    h: float
    description: str | None
    corrected: bool
    by_reviewer: bool  # added by an expert, not by the photo's author


class HistoryOut(BaseModel):
    decision: ReviewDecision
    comment: str | None
    reviewer: str | None
    image_version: int
    created_at: dt.datetime


class ReviewPhotoOut(BaseModel):
    id: UUID
    domain_id: UUID
    domain_name: str
    status: AtlasImageStatus
    version: int
    width: int
    height: int
    caption: str | None
    regions: list[ReviewRegionOut]
    privacy_flags: list[str]
    privacy_check: str
    approvals: int
    quorum: int
    is_own: bool
    history: list[HistoryOut]


class RegionEditIn(BaseModel):
    id: UUID | None = None
    label_id: UUID
    x: Unit
    y: Unit
    w: Unit
    h: Unit
    description: str | None = Field(default=None, max_length=1000)


class CorrectionIn(BaseModel):
    version: int = Field(ge=1)
    regions: list[RegionEditIn] = Field(max_length=50)


class DecisionIn(BaseModel):
    decision: ReviewDecision
    comment: str | None = Field(default=None, max_length=2000)
    version: int = Field(ge=1)


class DecisionOut(BaseModel):
    id: UUID
    previous: AtlasImageStatus
    status: AtlasImageStatus
    version: int
    approvals: int
    quorum: int


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@asynccontextmanager
async def _service(request: Request) -> AsyncGenerator[AtlasReviewService]:
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session, session.begin():
        yield AtlasReviewService(session, _settings(request).governance.review_quorum)


def _fail(error: ReviewError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.code})


async def _photo(service: AtlasReviewService, image: AtlasImage, viewer: UUID) -> ReviewPhotoOut:
    regions = await service.regions(image.id)
    labels = await service.labels(regions)
    return ReviewPhotoOut(
        id=image.id,
        domain_id=image.domain_id,
        domain_name=await service.domain_name(image.domain_id),
        status=image.status,
        version=image.version,
        width=image.width,
        height=image.height,
        caption=image.caption,
        regions=[
            ReviewRegionOut(
                id=r.id,
                label=ReviewLabelOut(
                    id=labels[r.label_id].id,
                    name=labels[r.label_id].name,
                    status=labels[r.label_id].status,
                ),
                x=r.x,
                y=r.y,
                w=r.w,
                h=r.h,
                description=r.description,
                corrected=r.corrected_by is not None,
                by_reviewer=r.author_id != image.author_id,
            )
            for r in regions
        ],
        privacy_flags=flagged(dict(image.privacy_flags)),
        privacy_check=str(image.privacy_flags.get("check", "unavailable")),
        approvals=await service.approvals(image),
        quorum=service.quorum,
        is_own=image.author_id == viewer,
        history=[
            HistoryOut(
                decision=review.decision,
                comment=review.comment,
                reviewer=name,
                image_version=review.image_version,
                created_at=review.created_at,
            )
            for review, name in await service.history(image.id)
        ],
    )


@router.get("/queue", response_model=list[QueuePhotoOut])
async def atlas_queue(request: Request, user: CurrentUser) -> list[QueuePhotoOut]:
    async with _service(request) as service:
        images = await service.queue()
        approvals = await service.approvals_by_image(images)
        out: list[QueuePhotoOut] = []
        for image in images:
            regions = await service.regions(image.id)
            labels = await service.labels(regions)
            out.append(
                QueuePhotoOut(
                    id=image.id,
                    domain_id=image.domain_id,
                    version=image.version,
                    regions=len(regions),
                    proposed_labels=sum(
                        labels[r.label_id].status == VisualLabelStatus.PROPOSED for r in regions
                    ),
                    privacy_flags=flagged(dict(image.privacy_flags)),
                    privacy_check=str(image.privacy_flags.get("check", "unavailable")),
                    approvals=approvals.get(image.id, 0),
                    quorum=service.quorum,
                    is_own=image.author_id == user.id,
                    submitted_at=image.submitted_at,
                )
            )
        return out


@router.get("/{image_id}", response_model=ReviewPhotoOut)
async def atlas_review_photo(image_id: UUID, request: Request, user: CurrentUser) -> ReviewPhotoOut:
    async with _service(request) as service:
        try:
            image = await service.image(image_id)
        except ReviewError as exc:
            raise _fail(exc) from None
        if image.status == AtlasImageStatus.DRAFT and image.author_id != user.id:
            raise HTTPException(status_code=404, detail={"error": "photo_not_found"})
        return await _photo(service, image, user.id)


@router.put("/{image_id}/regions", response_model=ReviewPhotoOut)
async def correct_regions(
    image_id: UUID, payload: CorrectionIn, request: Request, user: CurrentUser
) -> ReviewPhotoOut:
    """Replace the marks (send all of them; ``id`` for existing ones). Bumps the version, so
    earlier approvals no longer count."""
    edits = [
        RegionEdit(r.id, r.label_id, r.x, r.y, r.w, r.h, r.description) for r in payload.regions
    ]
    async with _service(request) as service:
        try:
            image = await service.correct(user.id, image_id, payload.version, edits)
        except ReviewError as exc:
            raise _fail(exc) from None
        return await _photo(service, image, user.id)


@router.post("/{image_id}/decision", response_model=DecisionOut)
async def decide(
    image_id: UUID, payload: DecisionIn, request: Request, user: CurrentUser
) -> DecisionOut:
    async with _service(request) as service:
        try:
            outcome = await service.decide(
                user.id, image_id, payload.decision, payload.comment, payload.version
            )
        except ReviewError as exc:
            raise _fail(exc) from None
        result = DecisionOut(
            id=image_id,
            previous=outcome.previous,
            status=outcome.status,
            version=outcome.version,
            approvals=outcome.approvals,
            quorum=outcome.quorum,
        )
    if result.status != result.previous:
        await invalidate_corpus(request.app.state.redis)  # after commit
    return result

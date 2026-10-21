"""``/atlas``: sending photos to the shared atlas (9.4) and atlas image bytes.

Submissions are private (RLS): another member's submission is a 404. Atlas image bytes follow
``atlas.access.can_view`` and are served like private photos (A2 headers).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Annotated, Any, Literal
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from spoldzielnia_ai.api.photos import IMAGE_HEADERS
from spoldzielnia_ai.atlas.access import can_view
from spoldzielnia_ai.atlas.annotations import AnnotationError
from spoldzielnia_ai.atlas.coverage import atlas_coverage
from spoldzielnia_ai.atlas.images import AtlasObjects
from spoldzielnia_ai.atlas.labels import LabelService
from spoldzielnia_ai.atlas.privacy import flagged, needs_people_confirmation
from spoldzielnia_ai.atlas.review import AtlasGallery
from spoldzielnia_ai.atlas.submissions import Choices, SubmissionError, SubmissionService
from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import (
    AtlasImageStatus,
    AtlasSubmissionStatus,
    ConsentStatus,
    ReviewDecision,
    Role,
    VisualLabelStatus,
)
from spoldzielnia_ai.db.models import (
    AnnotatedImage,
    AtlasImage,
    AtlasRegion,
    AtlasReview,
    User,
    VisualDomain,
)
from spoldzielnia_ai.db.rls import RlsSessionDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.notebook.deps import CipherDep, StorageDep  # noqa: TC001
from spoldzielnia_ai.shield.coverage import CoverageExceeded
from spoldzielnia_ai.shield.deps import coverage_refusal
from spoldzielnia_ai.workers.jobs import AtlasJob

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from spoldzielnia_ai.auth.deps import Principal
    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.models import AtlasSubmission
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue

router = APIRouter(prefix="/atlas", tags=["atlas"])


class SubmissionIn(BaseModel):
    note_id: UUID
    attachment_id: UUID


class SendIn(BaseModel):
    show_author: bool = Field(description="Show my name with the photo (no default: ask)")
    allow_export: bool = Field(description="Allow the photo in dataset exports (no default)")
    confirm_no_people: bool = False


class AtlasLabelOut(BaseModel):
    id: UUID
    name: str
    status: VisualLabelStatus


class AtlasRegionOut(BaseModel):
    id: UUID
    label: AtlasLabelOut
    x: float
    y: float
    w: float
    h: float
    description: str | None


class DraftOut(BaseModel):
    """What the atlas would show: the scrubbed texts and the checks."""

    id: UUID
    status: AtlasImageStatus
    width: int
    height: int
    caption: str | None
    regions: list[AtlasRegionOut]
    privacy_check: str  # ok | unavailable
    privacy_flags: list[str]
    needs_people_confirmation: bool
    show_author: bool
    allow_export: bool
    review_alert: str | None


class SubmissionOut(BaseModel):
    id: UUID
    status: AtlasSubmissionStatus
    error: str | None
    pii_replaced: int
    created_at: dt.datetime
    updated_at: dt.datetime
    atlas: DraftOut | None = None


def _fail(error: SubmissionError | AnnotationError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.code})


async def _within_daily_limit(redis: Redis, user_id: UUID, limit: int) -> bool:
    key = f"atlas:submissions:{user_id}:{dt.datetime.now(dt.UTC):%Y%m%d}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, 2 * 24 * 3600)
    return count <= limit


async def _out(
    service: SubmissionService, labels: LabelService, row: AtlasSubmission
) -> SubmissionOut:
    out = SubmissionOut(
        id=row.id,
        status=row.status,
        error=row.error,
        pii_replaced=sum(int(v) for v in row.pii_stats.values()),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
    image = await service.atlas_image(row)
    if image is None or (image.caption is None and image.object_key is None):
        return out
    regions = await service.regions(image)
    resolved = await labels.resolve({r.label_id for r in regions})
    flags: dict[str, Any] = dict(image.privacy_flags)
    out.atlas = DraftOut(
        id=image.id,
        status=image.status,
        width=image.width,
        height=image.height,
        caption=image.caption,
        regions=[
            AtlasRegionOut(
                id=r.id,
                label=AtlasLabelOut(
                    id=resolved[r.label_id].id,
                    name=resolved[r.label_id].name,
                    status=resolved[r.label_id].status,
                ),
                x=r.x,
                y=r.y,
                w=r.w,
                h=r.h,
                description=r.description,
            )
            for r in regions
        ],
        privacy_check=str(flags.get("check", "unavailable")),
        privacy_flags=flagged(flags),
        needs_people_confirmation=needs_people_confirmation(flags),
        show_author=image.show_author,
        allow_export=image.allow_export,
        review_alert=image.review_alert,
    )
    return out


@router.post("/submissions", status_code=status.HTTP_202_ACCEPTED, response_model=SubmissionOut)
async def request_submission(
    payload: SubmissionIn,
    request: Request,
    user: CurrentUser,
    session: RlsSessionDep,
    cipher: CipherDep,
    background: BackgroundTasks,
) -> SubmissionOut:
    """Prepare an annotated photo for the atlas (worker-core). Nothing is shared until the
    member sends the prepared result."""
    service = SubmissionService(session, cipher)
    try:
        row = await service.request(user.id, payload.note_id, payload.attachment_id)
    except (SubmissionError, AnnotationError) as exc:
        raise _fail(exc) from None
    settings: Settings = request.app.state.settings
    if not await _within_daily_limit(
        request.app.state.redis, user.id, settings.atlas.submissions_per_day
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "rate_limited"})
    queue: ProcessingQueue = request.app.state.processing_queue
    background.add_task(queue.atlas_requested, AtlasJob(submission_id=row.id, owner_id=user.id))
    return await _out(service, LabelService(session), row)


@router.get("/submissions", response_model=list[SubmissionOut])
async def photo_submissions(
    session: RlsSessionDep, cipher: CipherDep, attachment_id: UUID
) -> list[SubmissionOut]:
    """The submissions of one of my photos, newest first."""
    image_id = await session.scalar(
        select(AnnotatedImage.id).where(AnnotatedImage.attachment_id == attachment_id)
    )
    if image_id is None:
        return []
    service = SubmissionService(session, cipher)
    labels = LabelService(session)
    return [await _out(service, labels, row) for row in await service.list_for_photo(image_id)]


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
async def get_submission(
    submission_id: UUID, session: RlsSessionDep, cipher: CipherDep
) -> SubmissionOut:
    service = SubmissionService(session, cipher)
    try:
        return await _out(service, LabelService(session), await service.get(submission_id))
    except SubmissionError as exc:
        raise _fail(exc) from None


@router.post("/submissions/{submission_id}/send", response_model=SubmissionOut)
async def send_submission(
    submission_id: UUID,
    payload: SendIn,
    user: CurrentUser,
    session: RlsSessionDep,
    cipher: CipherDep,
) -> SubmissionOut:
    """Send the prepared photo to the experts. ``show_author`` and ``allow_export`` must be
    answered explicitly."""
    service = SubmissionService(session, cipher)
    choices = Choices(payload.show_author, payload.allow_export, payload.confirm_no_people)
    try:
        row = await service.send(user.id, submission_id, choices)
    except SubmissionError as exc:
        raise _fail(exc) from None
    return await _out(service, LabelService(session), row)


@router.post("/submissions/{submission_id}/withdraw", response_model=SubmissionOut)
async def withdraw_submission(
    submission_id: UUID,
    user: CurrentUser,
    session: RlsSessionDep,
    cipher: CipherDep,
    storage: StorageDep,
    background: BackgroundTasks,
) -> SubmissionOut:
    service = SubmissionService(session, cipher)
    try:
        keys = await service.withdraw(user.id, submission_id)
        row = await service.get(submission_id)
    except SubmissionError as exc:
        raise _fail(exc) from None
    background.add_task(AtlasObjects(cipher, storage).delete, keys)  # after commit
    return await _out(service, LabelService(session), row)


# --- The shared atlas (9.5) ----------------------------------------------------------------------


class PhotoCardOut(BaseModel):
    id: UUID
    domain_id: UUID
    caption: str | None
    labels: list[str]
    author_name: str | None  # only if the author chose to be named (A12)
    verified_at: dt.datetime | None
    width: int
    height: int


class PhotoPageOut(BaseModel):
    items: list[PhotoCardOut]
    total: int
    offset: int
    limit: int


class PhotoDetailOut(BaseModel):
    id: UUID
    domain_id: UUID
    domain_name: str
    status: AtlasImageStatus
    consent_status: ConsentStatus
    caption: str | None
    width: int
    height: int
    regions: list[AtlasRegionOut]
    author_name: str | None
    is_own: bool
    verified_at: dt.datetime | None
    reviewers: list[str]


def _privileged(image: AtlasImage, principal: Principal) -> bool:
    """Authors and reviewing experts don't spend coverage budget on a photo."""
    if image.author_id == principal.id:
        return True
    reviewing = image.status != AtlasImageStatus.APPROVED
    return (
        reviewing
        and principal.mfa_satisfied
        and bool({Role.EXPERT, Role.ADMIN} & set(principal.user.roles))
    )


async def _count_view(request: Request, principal: Principal, image: AtlasImage) -> None:
    if _privileged(image, principal):
        return
    try:
        await atlas_coverage(request).admit(principal.id, [image.id], [image.domain_id])
    except CoverageExceeded as exc:
        raise coverage_refusal(exc) from None


async def _within_hourly_views(request: Request, user_id: UUID) -> bool:
    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis
    key = f"atlas:views:{user_id}:{dt.datetime.now(dt.UTC):%Y%m%d%H}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, 2 * 3600)
    return count <= settings.atlas.image_views_per_hour


@router.get("/photos", response_model=PhotoPageOut)
async def atlas_photos(
    request: Request,
    _user: CurrentUser,
    domain_id: UUID | None = None,
    label_id: UUID | None = None,
    q: Annotated[str, Query(max_length=100)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
) -> PhotoPageOut:
    """Approved photos (thumbnails only in lists; no budget spent)."""
    settings: Settings = request.app.state.settings
    limit = min(limit, settings.atlas.page_size_max)
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        gallery = AtlasGallery(session)
        page = await gallery.search(
            domain_id=domain_id, label_id=label_id, q=q, offset=offset, limit=limit
        )
        labels = await gallery.label_names([i.id for i in page.items])
        authors = await gallery.authors(page.items)
    return PhotoPageOut(
        items=[
            PhotoCardOut(
                id=i.id,
                domain_id=i.domain_id,
                caption=(i.caption[:200] if i.caption else None),
                labels=labels.get(i.id, []),
                author_name=authors.get(i.id),
                verified_at=i.verified_at,
                width=i.width,
                height=i.height,
            )
            for i in page.items
        ],
        total=page.total,
        offset=offset,
        limit=limit,
    )


@router.get("/photos/{image_id}", response_model=PhotoDetailOut)
async def atlas_photo(image_id: UUID, request: Request, user: CurrentUser) -> PhotoDetailOut:
    """One photo with its marks. Counts against the viewer's coverage budget (A7)."""
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        image = await session.get(AtlasImage, image_id)
        if image is None or not can_view(image, user.id, user.user.roles, user.mfa_satisfied):
            raise HTTPException(status_code=404, detail={"error": "photo_not_found"})
        regions = (
            await session.scalars(
                select(AtlasRegion)
                .where(AtlasRegion.atlas_image_id == image.id)
                .order_by(AtlasRegion.position)
            )
        ).all()
        labels = await LabelService(session).resolve({r.label_id for r in regions})
        domain = await session.get(VisualDomain, image.domain_id)
        author = (await AtlasGallery(session).authors([image])).get(image.id)
        reviewer_rows = await session.execute(
            select(User.id, User.display_name, User.username)
            .join(AtlasReview, AtlasReview.reviewer_id == User.id)
            .where(
                AtlasReview.atlas_image_id == image.id,
                AtlasReview.decision == ReviewDecision.APPROVE,
                AtlasReview.image_version == image.version,
            )
            .distinct()
        )
        reviewers = [display or username or "Ekspert" for _, display, username in reviewer_rows]
    await _count_view(request, user, image)
    return PhotoDetailOut(
        id=image.id,
        domain_id=image.domain_id,
        domain_name=domain.name if domain else "",
        status=image.status,
        consent_status=image.consent_status,
        caption=image.caption,
        width=image.width,
        height=image.height,
        regions=[
            AtlasRegionOut(
                id=r.id,
                label=AtlasLabelOut(
                    id=labels[r.label_id].id,
                    name=labels[r.label_id].name,
                    status=labels[r.label_id].status,
                ),
                x=r.x,
                y=r.y,
                w=r.w,
                h=r.h,
                description=r.description,
            )
            for r in regions
        ],
        author_name=author,
        is_own=image.author_id == user.id,
        verified_at=image.verified_at,
        reviewers=reviewers,
    )


@router.get(
    "/images/{image_id}/image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def atlas_image_bytes(
    image_id: UUID,
    request: Request,
    user: CurrentUser,
    cipher: CipherDep,
    storage: StorageDep,
    size: Annotated[Literal["thumb", "preview", "full"], Query()] = "preview",
) -> Response:
    """An atlas photo (metadata-free JPEG) for whoever may see it (A5); 404 otherwise.
    Preview and full images of other members' photos are rate limited and counted (A7)."""
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as db:
        image = await db.get(AtlasImage, image_id)
    if image is None or not can_view(image, user.id, user.user.roles, user.mfa_satisfied):
        raise HTTPException(status_code=404, detail={"error": "photo_not_found"})
    if size != "thumb" and not _privileged(image, user):
        if not await _within_hourly_views(request, user.id):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": "rate_limited"},
                headers={"Retry-After": "600"},
            )
        await _count_view(request, user, image)
    data = await AtlasObjects(cipher, storage).read(image, size)
    if data is None:
        raise HTTPException(status_code=404, detail={"error": "photo_not_found"})
    return Response(data, media_type="image/jpeg", headers=IMAGE_HEADERS)

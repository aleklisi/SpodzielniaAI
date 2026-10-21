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
from spoldzielnia_ai.atlas.images import AtlasObjects
from spoldzielnia_ai.atlas.labels import LabelService
from spoldzielnia_ai.atlas.privacy import flagged, needs_people_confirmation
from spoldzielnia_ai.atlas.submissions import Choices, SubmissionError, SubmissionService
from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import (  # noqa: TC001 - pydantic resolves annotations
    AtlasImageStatus,
    AtlasSubmissionStatus,
    VisualLabelStatus,
)
from spoldzielnia_ai.db.models import AnnotatedImage, AtlasImage
from spoldzielnia_ai.db.rls import RlsSessionDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.notebook.deps import CipherDep, StorageDep  # noqa: TC001
from spoldzielnia_ai.workers.jobs import AtlasJob

if TYPE_CHECKING:
    from redis.asyncio import Redis

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
    """An atlas photo (metadata-free JPEG) for whoever may see it (A5); 404 otherwise."""
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as db:
        image = await db.get(AtlasImage, image_id)
    if image is None or not can_view(image, user.id, user.user.roles, user.mfa_satisfied):
        raise HTTPException(status_code=404, detail={"error": "photo_not_found"})
    data = await AtlasObjects(cipher, storage).read(image, size)
    if data is None:
        raise HTTPException(status_code=404, detail={"error": "photo_not_found"})
    return Response(data, media_type="image/jpeg", headers=IMAGE_HEADERS)

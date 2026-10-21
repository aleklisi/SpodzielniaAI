"""Photos in the private notebook: image bytes and annotations (chunk 9.2). Owner only.

Another member's photo answers exactly like a missing one (404). Image bytes are served with
headers that keep the browser from treating them as anything but an image (A2).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from spoldzielnia_ai.atlas.annotations import (
    MAX_CAPTION,
    MAX_DESCRIPTION,
    MAX_REGIONS,
    AnnotationError,
    AnnotationService,
    PhotoView,
    RegionIn,
)
from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import AnnotationSource, VisualLabelStatus
from spoldzielnia_ai.db.rls import RlsSessionDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.notebook.deps import CipherDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.notebook.images import PrivateImages
from spoldzielnia_ai.vector.deps import QdrantDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.vector.repositories import PrivateVectorRepo
from spoldzielnia_ai.workers.jobs import DerivativeJob

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue

router = APIRouter(prefix="/notes", tags=["photos"])
log = get_logger(__name__)

IMAGE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "inline",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class LabelRefOut(BaseModel):
    id: UUID
    name: str
    status: VisualLabelStatus


class RegionOut(BaseModel):
    id: UUID
    label: LabelRefOut
    x: float
    y: float
    w: float
    h: float
    description: str | None
    source: AnnotationSource


class PhotoOut(BaseModel):
    attachment_id: UUID
    note_id: UUID
    mime_type: str
    width: int | None
    height: int | None
    domain_id: UUID | None
    caption: str | None
    version: int = Field(description="0 until the first save; send it back as `version`")
    regions: list[RegionOut]
    updated_at: datetime | None

    @classmethod
    def of(cls, view: PhotoView) -> PhotoOut:
        return cls.model_validate(view, from_attributes=True)


Coordinate = Annotated[float, Field(ge=0, le=1)]


class RegionIn_(BaseModel):
    label_id: UUID
    x: Coordinate
    y: Coordinate
    w: Coordinate
    h: Coordinate
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    source: AnnotationSource = AnnotationSource.USER


class PhotoIn(BaseModel):
    domain_id: UUID
    caption: str | None = Field(default=None, max_length=MAX_CAPTION)
    regions: list[RegionIn_] = Field(default=[], max_length=MAX_REGIONS)
    version: int = Field(ge=0, description="The version the editor loaded (0 for a new photo)")


def _fail(error: AnnotationError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.code})


def _images(request: Request) -> PrivateImages:
    state = request.app.state
    settings: Settings = state.settings
    return PrivateImages(
        state.session_factory, state.cipher, state.storage, settings.uploads.max_image_pixels
    )


@router.get(
    "/{note_id}/attachments/{attachment_id}/image",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}, "image/webp": {}}}},
)
async def photo_image(
    note_id: UUID,
    attachment_id: UUID,
    request: Request,
    user: CurrentUser,
    size: Annotated[Literal["preview", "full"], Query()] = "preview",
) -> Response:
    """The photo for display: ``preview`` (JPEG, ≤ 1600 px) or ``full`` (the stored image;
    HEIC as a JPEG of ≤ 4096 px)."""
    image = await _images(request).get(user.id, note_id, attachment_id, full=size == "full")
    if image is None:
        raise HTTPException(status_code=404, detail={"error": "photo_not_found"})
    return Response(image.data, media_type=image.mime_type, headers=IMAGE_HEADERS)


@router.get("/{note_id}/attachments/{attachment_id}/annotations", response_model=PhotoOut)
async def get_annotations(
    note_id: UUID, attachment_id: UUID, session: RlsSessionDep, cipher: CipherDep
) -> PhotoOut:
    try:
        return PhotoOut.of(await AnnotationService(session, cipher).get(note_id, attachment_id))
    except AnnotationError as exc:
        raise _fail(exc) from None


async def _drop_vectors(client: AsyncQdrantClient, owner_id: UUID, derivative_id: UUID) -> None:
    try:
        await PrivateVectorRepo(client, owner_id).delete_derivative(derivative_id)
    except Exception as exc:  # orphaned points are filtered by RLS on read; reindex drops them
        log.warning("photos.vector_delete_failed", error=type(exc).__name__)


@router.put("/{note_id}/attachments/{attachment_id}/annotations", response_model=PhotoOut)
async def save_annotations(
    note_id: UUID,
    attachment_id: UUID,
    payload: PhotoIn,
    request: Request,
    user: CurrentUser,
    session: RlsSessionDep,
    cipher: CipherDep,
    qdrant: QdrantDep,
    background: BackgroundTasks,
) -> PhotoOut:
    """Replace the caption and every mark (the editor sends all of them). 409
    ``stale_version`` if the photo changed since the editor loaded it."""
    regions = [
        RegionIn(r.label_id, r.x, r.y, r.w, r.h, r.description, r.source) for r in payload.regions
    ]
    try:
        result = await AnnotationService(session, cipher).save(
            user.id,
            note_id,
            attachment_id,
            domain_id=payload.domain_id,
            caption=payload.caption,
            regions=regions,
            expected_version=payload.version,
        )
    except AnnotationError as exc:
        raise _fail(exc) from None
    # Background tasks run after the RLS transaction has committed.
    queue: ProcessingQueue = request.app.state.processing_queue
    if result.derivative_id is not None:
        job = DerivativeJob(derivative_id=result.derivative_id, owner_id=user.id)
        background.add_task(queue.annotations_saved, job)
    if result.dropped_derivative_id is not None:
        background.add_task(_drop_vectors, qdrant, user.id, result.dropped_derivative_id)
    return PhotoOut.of(result.photo)

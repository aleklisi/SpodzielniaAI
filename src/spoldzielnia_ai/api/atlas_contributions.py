"""``/me/contributions/atlas``: the author's atlas photos and their consent (chunk 9.6).

Suspend, resume, revoke (irreversible: ``confirm: "REVOKE"`` and a recent login, as for wiki
blocks), and the two author choices: name shown with the photo, use in dataset exports.
"""

from __future__ import annotations

import datetime as dt  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from spoldzielnia_ai.api.contributions import recently_authenticated
from spoldzielnia_ai.atlas.consent import AtlasConsentService, PhotoConsentAction, PhotoConsentError
from spoldzielnia_ai.atlas.coverage import invalidate_corpus
from spoldzielnia_ai.atlas.images import AtlasObjects
from spoldzielnia_ai.atlas.review import AtlasGallery
from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import (
    AtlasImageStatus,
    ConsentStatus,
)
from spoldzielnia_ai.notebook.deps import CipherDep, StorageDep  # noqa: TC001
from spoldzielnia_ai.vector.deps import VectorSyncDep  # noqa: TC001 - FastAPI resolves it

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.models import AtlasImage
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.vector.sync import VectorSync

router = APIRouter(prefix="/me/contributions/atlas", tags=["contributions"])


class MyPhotoOut(BaseModel):
    id: UUID
    status: AtlasImageStatus
    consent_status: ConsentStatus
    labels: list[str]
    caption: str | None
    show_author: bool
    allow_export: bool
    review_alert: str | None
    submitted_at: dt.datetime | None
    verified_at: dt.datetime | None


class PreferencesIn(BaseModel):
    show_author: bool | None = None
    allow_export: bool | None = None


class RevokeIn(BaseModel):
    confirm: Literal["REVOKE"]


def _fail(error: PhotoConsentError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.code})


async def _out(gallery: AtlasGallery, images: list[AtlasImage]) -> list[MyPhotoOut]:
    labels = await gallery.label_names([i.id for i in images])
    return [
        MyPhotoOut(
            id=i.id,
            status=i.status,
            consent_status=i.consent_status,
            labels=labels.get(i.id, []),
            caption=i.caption,
            show_author=i.show_author,
            allow_export=i.allow_export,
            review_alert=i.review_alert,
            submitted_at=i.submitted_at,
            verified_at=i.verified_at,
        )
        for i in images
    ]


@router.get("", response_model=list[MyPhotoOut])
async def my_atlas_photos(request: Request, user: CurrentUser) -> list[MyPhotoOut]:
    """Every atlas photo the member sent (drafts excluded), newest first."""
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        images = [
            i
            for i in await AtlasConsentService(session).mine(user.id)
            if i.status != AtlasImageStatus.DRAFT
        ]
        return await _out(AtlasGallery(session), images)


async def _apply(
    action: PhotoConsentAction,
    image_id: UUID,
    request: Request,
    user_id: UUID,
    cipher: CipherDep,
    storage: StorageDep,
    background: BackgroundTasks,
    sync: VectorSync,
) -> MyPhotoOut:
    factory: SessionFactory = request.app.state.session_factory
    try:
        async with factory() as session, session.begin():
            result = await AtlasConsentService(session).change(action, user_id, image_id)
            [out] = await _out(AtlasGallery(session), [result.image])
            seqs = await sync.record(session, result.ops)
    except PhotoConsentError as exc:
        raise _fail(exc) from None
    # The visual index follows at once (suspended or revoked photos stop being cited).
    await sync.flush(seqs)
    # After commit: the objects of a revoked photo, and the coverage denominator.
    if result.object_keys:
        background.add_task(AtlasObjects(cipher, storage).delete, result.object_keys)
    await invalidate_corpus(request.app.state.redis)
    return out


@router.post("/{image_id}/suspend", response_model=MyPhotoOut)
async def suspend_photo(
    image_id: UUID,
    request: Request,
    user: CurrentUser,
    cipher: CipherDep,
    storage: StorageDep,
    background: BackgroundTasks,
    sync: VectorSyncDep,
) -> MyPhotoOut:
    """Hide the photo from the atlas, recognition and exports until resumed."""
    return await _apply(
        PhotoConsentAction.SUSPEND, image_id, request, user.id, cipher, storage, background, sync
    )


@router.post("/{image_id}/resume", response_model=MyPhotoOut)
async def resume_photo(
    image_id: UUID,
    request: Request,
    user: CurrentUser,
    cipher: CipherDep,
    storage: StorageDep,
    background: BackgroundTasks,
    sync: VectorSyncDep,
) -> MyPhotoOut:
    return await _apply(
        PhotoConsentAction.RESUME, image_id, request, user.id, cipher, storage, background, sync
    )


@router.post("/{image_id}/revoke", response_model=MyPhotoOut)
async def revoke_photo(
    image_id: UUID,
    payload: RevokeIn,
    request: Request,
    user: CurrentUser,
    cipher: CipherDep,
    storage: StorageDep,
    background: BackgroundTasks,
    sync: VectorSyncDep,
) -> JSONResponse | MyPhotoOut:
    """Irreversible: the image, its texts and its marks are deleted from the atlas."""
    del payload  # its validation is the confirmation
    settings: Settings = request.app.state.settings
    if not recently_authenticated(user, settings):
        step_up = f"{settings.root_path}/auth/login?prompt=login"
        return JSONResponse(
            {"detail": {"error": "reauthentication_required", "step_up": step_up}},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return await _apply(
        PhotoConsentAction.REVOKE, image_id, request, user.id, cipher, storage, background, sync
    )


@router.patch("/{image_id}", response_model=MyPhotoOut)
async def photo_preferences(
    image_id: UUID, payload: PreferencesIn, request: Request, user: CurrentUser
) -> MyPhotoOut:
    """Name shown with the photo or not (A12); use in dataset exports or not (A9)."""
    factory: SessionFactory = request.app.state.session_factory
    try:
        async with factory() as session, session.begin():
            image = await AtlasConsentService(session).preferences(
                user.id,
                image_id,
                show_author=payload.show_author,
                allow_export=payload.allow_export,
            )
            [out] = await _out(AtlasGallery(session), [image])
    except PhotoConsentError as exc:
        raise _fail(exc) from None
    return out

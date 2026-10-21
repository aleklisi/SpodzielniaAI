"""``/admin/atlas/exports``: dataset exports of the atlas (9.7, A9). ADMIN + MFA.

Off unless the operator sets ``atlas.export_enabled`` (env); the UI cannot turn it on.
Creating an export needs a fresh login (step-up) and is limited per admin and day. The archive
is downloadable once, with the token shown at creation, until it expires.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import BackgroundTasks, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from spoldzielnia_ai.atlas.export import ExportError, ExportService, read_archive, token_hash
from spoldzielnia_ai.atlas.images import AtlasObjects
from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router, step_up_url
from spoldzielnia_ai.db.enums import (
    AtlasExportFormat,
    AtlasExportStatus,
    Role,
)
from spoldzielnia_ai.db.models import AtlasExport, User, VisualDomain
from spoldzielnia_ai.notebook.deps import CipherDep, StorageDep  # noqa: TC001
from spoldzielnia_ai.workers.jobs import ExportJob

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from spoldzielnia_ai.auth.deps import Principal
    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue

router = elevated_router(Role.ADMIN, prefix="/admin/atlas/exports", tags=["atlas"])


class ExportIn(BaseModel):
    format: AtlasExportFormat
    domain_id: UUID
    label_ids: list[UUID] | None = Field(default=None, max_length=200)
    min_verified_at: dt.datetime | None = None


class ExportOut(BaseModel):
    id: UUID
    status: AtlasExportStatus
    format: AtlasExportFormat
    domain_id: UUID
    domain_name: str
    created_by_name: str | None
    image_count: int
    region_count: int
    revoked_since: int  # photos withdrawn (or no longer exportable) since the export
    manifest_sha256: str | None
    expires_at: dt.datetime
    downloaded_at: dt.datetime | None
    created_at: dt.datetime
    error: str | None


class ExportListOut(BaseModel):
    enabled: bool
    exports: list[ExportOut]


class CreatedOut(BaseModel):
    export: ExportOut
    download_token: str = Field(description="Shown once; the download works once.")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _fail(error: ExportError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.code})


def _require_enabled(settings: Settings) -> None:
    if not settings.atlas.export_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "export_disabled"})


def _require_fresh_login(principal: Principal, settings: Settings) -> None:
    auth_time = principal.session.auth_time
    max_age = settings.atlas.export_step_up_max_age_seconds
    if auth_time is None or time.time() - auth_time > max_age:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"error": "reauthentication_required", "step_up": step_up_url(settings)},
        )


async def _within_daily_limit(redis: Redis, user_id: UUID, limit: int) -> bool:
    key = f"atlas:exports:{user_id}:{dt.datetime.now(dt.UTC):%Y%m%d}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, 2 * 24 * 3600)
    return count <= limit


async def _outs(
    service: ExportService, request: Request, exports: list[AtlasExport]
) -> list[ExportOut]:
    factory: SessionFactory = request.app.state.session_factory
    revoked = await service.revoked_counts(exports)
    async with factory() as session:
        domains = {
            uid: name
            for uid, name in await session.execute(select(VisualDomain.id, VisualDomain.name))
        }
        creators = {
            uid: display or username
            for uid, display, username in await session.execute(
                select(User.id, User.display_name, User.username).where(
                    User.id.in_({e.created_by for e in exports})
                )
            )
        }
    return [
        ExportOut(
            id=e.id,
            status=e.status,
            format=e.format,
            domain_id=e.domain_id,
            domain_name=domains.get(e.domain_id, ""),
            created_by_name=creators.get(e.created_by),
            image_count=e.image_count,
            region_count=e.region_count,
            revoked_since=revoked.get(e.id, 0),
            manifest_sha256=e.manifest_sha256,
            expires_at=e.expires_at,
            downloaded_at=e.downloaded_at,
            created_at=e.created_at,
            error=e.error,
        )
        for e in exports
    ]


@router.get("", response_model=ExportListOut)
async def list_exports(
    request: Request, cipher: CipherDep, storage: StorageDep, background: BackgroundTasks
) -> ExportListOut:
    settings = _settings(request)
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session, session.begin():
        service = ExportService(session, cipher, settings.atlas)
        expired = await service.expire()
        exports = list(await service.list())
    background.add_task(AtlasObjects(cipher, storage).delete, expired)
    async with factory() as session:
        service = ExportService(session, cipher, settings.atlas)
        return ExportListOut(
            enabled=settings.atlas.export_enabled, exports=await _outs(service, request, exports)
        )


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreatedOut)
async def create_export(
    payload: ExportIn,
    request: Request,
    user: CurrentUser,
    cipher: CipherDep,
    background: BackgroundTasks,
) -> CreatedOut:
    """Build an export of approved, consented, export-allowed photos of one domain. The
    download token in the response is shown once."""
    settings = _settings(request)
    _require_enabled(settings)
    _require_fresh_login(user, settings)
    if not await _within_daily_limit(
        request.app.state.redis, user.id, settings.atlas.exports_per_day
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "rate_limited"})
    factory: SessionFactory = request.app.state.session_factory
    try:
        async with factory() as session, session.begin():
            service = ExportService(session, cipher, settings.atlas)
            requested = await service.request(
                user.id,
                payload.format,
                payload.domain_id,
                payload.label_ids,
                payload.min_verified_at,
            )
    except ExportError as exc:
        raise _fail(exc) from None
    queue: ProcessingQueue = request.app.state.processing_queue
    background.add_task(queue.export_requested, ExportJob(export_id=requested.export.id))
    async with factory() as session:
        [out] = await _outs(
            ExportService(session, cipher, settings.atlas), request, [requested.export]
        )
    return CreatedOut(export=out, download_token=requested.token)


@router.get(
    "/{export_id}/download",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def download_export(
    export_id: UUID,
    request: Request,
    user: CurrentUser,
    cipher: CipherDep,
    storage: StorageDep,
    background: BackgroundTasks,
    token: Annotated[str, Query(min_length=20, max_length=100)],
) -> Response:
    """The archive, once. The token is checked before anything is read; the export is marked
    downloaded only after the archive was read, so a storage error doesn't burn the link."""
    settings = _settings(request)
    _require_enabled(settings)
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        export = await session.get(AtlasExport, export_id)
    if export is None or export.token_sha256 != token_hash(token):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "export_not_found"})
    if export.status != AtlasExportStatus.READY:
        code = (
            "already_downloaded" if export.status == AtlasExportStatus.DOWNLOADED else "not_ready"
        )
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": code})
    data = await read_archive(cipher, storage, export)
    try:
        async with factory() as session, session.begin():
            _, key = await ExportService(session, cipher, settings.atlas).claim_download(
                user.id, export_id, token
            )
    except ExportError as exc:
        raise _fail(exc) from None
    if key:
        background.add_task(AtlasObjects(cipher, storage).delete, [key])
    name = (
        f"atlas-{export.format.value.lower()}-{export.created_at:%Y%m%d}-{str(export.id)[-8:]}.zip"
    )
    return Response(
        data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )

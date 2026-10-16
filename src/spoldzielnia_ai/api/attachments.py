"""``/notes/{note_id}/attachments``: uploads into the private notebook (chunk 2.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves path parameter annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from spoldzielnia_ai.api.notes import AttachmentOut, DerivativeOut, not_found
from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.files.multipart import UploadError, read_upload
from spoldzielnia_ai.notebook.deps import NotebookDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.notebook.uploads import UploadPipeline, UploadRejected

if TYPE_CHECKING:
    import asyncio

    from spoldzielnia_ai.core.settings import Settings

router = APIRouter(prefix="/notes", tags=["attachments"])

TRUE_VALUES = frozenset({"true", "1", "on", "yes"})


def get_upload_pipeline(request: Request) -> UploadPipeline:
    state = request.app.state
    settings: Settings = state.settings
    return UploadPipeline(
        state.session_factory,
        state.cipher,
        state.storage,
        state.scanner,
        settings.uploads,
        state.audit_sink,
        state.processing_queue,
    )


UploadPipelineDep = Annotated[UploadPipeline, Depends(get_upload_pipeline)]


class AttachmentStatusOut(BaseModel):
    attachment: AttachmentOut
    derivatives: list[DerivativeOut]


def _error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": {"error": code}}, status_code=status_code)


@router.post(
    "/{note_id}/attachments",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AttachmentOut,
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "keep_location": {"type": "boolean", "default": False},
                        },
                    }
                }
            }
        }
    },
)
async def upload_attachment(
    note_id: UUID, request: Request, user: CurrentUser, pipeline: UploadPipelineDep
) -> JSONResponse:
    """Multipart fields: ``file`` and ``keep_location`` (default false). 202: the file is
    stored (clean) and processing has been queued."""
    if not await pipeline.note_exists(user.id, note_id):
        raise not_found()  # before reading a single byte of the body

    settings: Settings = request.app.state.settings
    slots: asyncio.Semaphore = request.app.state.upload_slots
    async with slots:
        try:
            upload = await read_upload(
                request.headers.get("content-type"),
                request.stream(),
                max_bytes=settings.uploads.max_bytes,
                allowed_fields=frozenset({"keep_location"}),
                spool_dir=settings.uploads.tmp_dir,
            )
        except UploadError as exc:
            rejected = await pipeline.reject(user.id, note_id, exc.code, exc.status)
            return _error(rejected.code, rejected.status)
        try:
            keep_location = upload.fields.get("keep_location", "").strip().lower() in TRUE_VALUES
            view = await pipeline.store(user.id, note_id, upload, keep_location)
        except UploadRejected as exc:
            return _error(exc.code, exc.status)
        finally:
            upload.cleanup()
    body = AttachmentOut.model_validate(view, from_attributes=True)
    return JSONResponse(body.model_dump(mode="json"), status_code=status.HTTP_202_ACCEPTED)


@router.get("/{note_id}/attachments/{attachment_id}", response_model=AttachmentStatusOut)
async def attachment_status(
    note_id: UUID, attachment_id: UUID, notebook: NotebookDep
) -> AttachmentStatusOut:
    """The attachment and the processing status of its derivatives."""
    found = await notebook.get_attachment(note_id, attachment_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    attachment, derivatives = found
    return AttachmentStatusOut(
        attachment=AttachmentOut.model_validate(attachment, from_attributes=True),
        derivatives=[DerivativeOut.model_validate(d, from_attributes=True) for d in derivatives],
    )

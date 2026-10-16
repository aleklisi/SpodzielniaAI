"""``/notes``: the private notebook (AGENTS.md §3.1: every handler runs in an RLS session).

Another user's note answers exactly like a missing one: 404, never 403 (no existence oracle).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator

from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import (  # noqa: TC001 - pydantic resolves them
    DerivativeKind,
    DerivativeStatus,
    ScanStatus,
)
from spoldzielnia_ai.notebook.deps import NotebookDep, StorageDep  # noqa: TC001

if TYPE_CHECKING:
    from typing import Self

    from spoldzielnia_ai.files.storage import ObjectStorage
    from spoldzielnia_ai.notebook.service import NoteView

router = APIRouter(prefix="/notes", tags=["notes"])

TITLE_MAX = 200
BODY_MAX = 200_000


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    body: str = Field(default="", max_length=BODY_MAX)


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    body: str | None = Field(default=None, max_length=BODY_MAX)

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.title is None and self.body is None:
            raise ValueError("nothing to update")
        return self


class NoteSummaryOut(BaseModel):
    id: UUID
    title: str
    source_type: str
    created_at: datetime
    updated_at: datetime


class AttachmentOut(BaseModel):
    id: UUID
    note_id: UUID
    filename: str
    mime_type: str
    size: int
    scan_status: ScanStatus
    keep_location: bool
    created_at: datetime


class DerivativeOut(BaseModel):
    id: UUID
    attachment_id: UUID | None
    kind: DerivativeKind
    status: DerivativeStatus
    source: str | None
    content: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class NoteOut(BaseModel):
    id: UUID
    title: str
    body: str
    source_type: str
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentOut]
    derivatives: list[DerivativeOut]

    @classmethod
    def of(cls, view: NoteView) -> NoteOut:
        return cls(
            id=view.id,
            title=view.title,
            body=view.body,
            source_type=view.source_type,
            created_at=view.created_at,
            updated_at=view.updated_at,
            attachments=[
                AttachmentOut.model_validate(a, from_attributes=True) for a in view.attachments
            ],
            derivatives=[
                DerivativeOut.model_validate(d, from_attributes=True) for d in view.derivatives
            ],
        )


def not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")


async def delete_objects(storage: ObjectStorage, keys: list[str]) -> None:
    for key in keys:
        await storage.delete(key)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=NoteOut)
async def create_note(payload: NoteCreate, user: CurrentUser, notebook: NotebookDep) -> NoteOut:
    return NoteOut.of(await notebook.create(user.id, payload.title, payload.body))


@router.get("", response_model=list[NoteSummaryOut])
async def list_notes(notebook: NotebookDep) -> list[NoteSummaryOut]:
    return [NoteSummaryOut.model_validate(n, from_attributes=True) for n in await notebook.list()]


@router.get("/{note_id}", response_model=NoteOut)
async def get_note(note_id: UUID, notebook: NotebookDep) -> NoteOut:
    view = await notebook.get(note_id)
    if view is None:
        raise not_found()
    return NoteOut.of(view)


@router.patch("/{note_id}", response_model=NoteOut)
async def update_note(note_id: UUID, payload: NoteUpdate, notebook: NotebookDep) -> NoteOut:
    view = await notebook.update(note_id, payload.title, payload.body)
    if view is None:
        raise not_found()
    return NoteOut.of(view)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: UUID, notebook: NotebookDep, storage: StorageDep, background: BackgroundTasks
) -> Response:
    keys = await notebook.delete(note_id)
    if keys is None:
        raise not_found()
    # Runs after the response, i.e. after the RLS transaction has committed the row deletion.
    background.add_task(delete_objects, storage, keys)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

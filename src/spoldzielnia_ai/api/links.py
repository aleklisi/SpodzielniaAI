"""``POST /notes/{note_id}/links``: queue a web page for SSRF-safe fetching (chunk 2.4).

The API only runs the syntactic SSRF checks: it has no internet access and resolves only
internal names. DNS validation, IP pinning and the fetch itself happen in the egress worker.
"""

from __future__ import annotations

from uuid import UUID  # noqa: TC003 - FastAPI resolves path parameter annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from spoldzielnia_ai.api.notes import DerivativeOut, not_found
from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.core.settings import Settings  # noqa: TC001 - annotation of app state
from spoldzielnia_ai.db.rls import rls_session
from spoldzielnia_ai.net.safe_fetch import SsrfBlocked, check_url
from spoldzielnia_ai.notebook.service import NotebookService
from spoldzielnia_ai.notebook.uploads import ProcessingQueue  # noqa: TC001

router = APIRouter(prefix="/notes", tags=["links"])


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/{note_id}/links", status_code=status.HTTP_202_ACCEPTED, response_model=DerivativeOut)
async def add_link(
    note_id: UUID, payload: LinkIn, request: Request, user: CurrentUser
) -> JSONResponse:
    state = request.app.state
    settings: Settings = state.settings
    try:
        target = check_url(payload.url.strip(), settings.fetch)
    except SsrfBlocked as exc:
        return JSONResponse(
            {"detail": {"error": "ssrf_blocked", "reason": exc.reason}},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    # Own transaction, committed before the task is sent: the worker must find the row.
    async with rls_session(state.session_factory, user.id) as session:
        view = await NotebookService(session, state.cipher).add_link(note_id, target.url)
    if view is None:
        raise not_found()
    queue: ProcessingQueue = state.processing_queue
    await queue.link_added(view.id, user.id)
    body = DerivativeOut.model_validate(view, from_attributes=True)
    return JSONResponse(body.model_dump(mode="json"), status_code=status.HTTP_202_ACCEPTED)

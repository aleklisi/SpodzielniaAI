"""„Rozpoznaj” on a notebook photo (chunk 9.9). Owner only (RLS); 404 for anyone else.

The answer holds the local model's text, suggested marks (label + box) and the atlas photos it
rests on. Citations are re-checked at read time (``can_view``), name their author only if the
author chose so (A12), and spend the viewer's coverage budget like opening the photos (A7).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from spoldzielnia_ai.api.capabilities import require_feature
from spoldzielnia_ai.atlas.access import can_view
from spoldzielnia_ai.atlas.coverage import atlas_coverage
from spoldzielnia_ai.atlas.recognition import MAX_QUESTION, RecognitionError, RecognitionService
from spoldzielnia_ai.atlas.review import AtlasGallery
from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import DerivativeStatus  # noqa: TC001 - pydantic
from spoldzielnia_ai.db.models import AtlasImage
from spoldzielnia_ai.db.rls import RlsSessionDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.notebook.deps import CipherDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.shield.coverage import CoverageExceeded
from spoldzielnia_ai.shield.deps import coverage_refusal
from spoldzielnia_ai.workers.jobs import DerivativeJob

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue

router = APIRouter(prefix="/notes", tags=["photos"])


class RecognizeIn(BaseModel):
    question: str | None = Field(default=None, max_length=MAX_QUESTION)
    domain_id: UUID | None = None  # default: the photo's domain
    all_domains: bool = False


class SuggestionOut(BaseModel):
    label_id: UUID
    label_name: str
    x: float
    y: float
    w: float
    h: float
    confidence: float


class CitationOut(BaseModel):
    image_id: UUID
    labels: list[str]
    author_name: str | None


class RecognitionOut(BaseModel):
    id: UUID
    status: DerivativeStatus
    error: str | None
    question: str
    answer: str | None
    grounded: bool
    vlm: str | None
    suggestions: list[SuggestionOut]
    citations: list[CitationOut]
    created_at: dt.datetime


def _fail(error: RecognitionError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error": error.code})


async def _within_daily_limit(redis: Redis, user_id: UUID, limit: int) -> bool:
    key = f"atlas:recognitions:{user_id}:{dt.datetime.now(dt.UTC):%Y%m%d}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, 2 * 24 * 3600)
    return count <= limit


@router.post(
    "/{note_id}/attachments/{attachment_id}/recognize",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RecognitionOut,
)
async def recognize(
    note_id: UUID,
    attachment_id: UUID,
    payload: RecognizeIn,
    request: Request,
    user: CurrentUser,
    session: RlsSessionDep,
    cipher: CipherDep,
    background: BackgroundTasks,
) -> RecognitionOut:
    """Ask what's on the photo, answered from verified atlas examples by a local model."""
    await require_feature(request, "recognition")  # 503 model_not_configured, nothing created
    try:
        row = await RecognitionService(session, cipher).request(
            user.id,
            note_id,
            attachment_id,
            question=payload.question,
            domain_id=payload.domain_id,
            all_domains=payload.all_domains,
        )
    except RecognitionError as exc:
        raise _fail(exc) from None
    settings: Settings = request.app.state.settings
    if not await _within_daily_limit(
        request.app.state.redis, user.id, settings.atlas.recognitions_per_day
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail={"error": "rate_limited"})
    queue: ProcessingQueue = request.app.state.processing_queue
    background.add_task(
        queue.recognition_requested, DerivativeJob(derivative_id=row.id, owner_id=user.id)
    )
    return RecognitionOut(
        id=row.id,
        status=row.status,
        error=None,
        question=(payload.question or "").strip(),
        answer=None,
        grounded=False,
        vlm=None,
        suggestions=[],
        citations=[],
        created_at=row.created_at,
    )


@router.get(
    "/{note_id}/attachments/{attachment_id}/recognitions", response_model=list[RecognitionOut]
)
async def recognitions(
    note_id: UUID,
    attachment_id: UUID,
    request: Request,
    user: CurrentUser,
    session: RlsSessionDep,
    cipher: CipherDep,
) -> list[RecognitionOut]:
    """The photo's recognitions, newest first (pending ones without a result)."""
    rows = await RecognitionService(session, cipher).list(note_id, attachment_id)
    cited = {
        UUID(str(c["image_id"]))
        for _, _, result in rows
        if result
        for c in result.get("citations", [])
    }
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as db:
        images = (
            {i.id: i for i in await db.scalars(select(AtlasImage).where(AtlasImage.id.in_(cited)))}
            if cited
            else {}
        )
        visible = {
            i.id: i
            for i in images.values()
            if can_view(i, user.id, user.user.roles, user.mfa_satisfied)
        }
        authors = await AtlasGallery(db).authors(list(visible.values()))
    others = [i.id for i in visible.values() if i.author_id != user.id]
    if others:
        domains = list({visible[i].domain_id for i in others})
        try:
            await atlas_coverage(request).admit(user.id, others, domains)
        except CoverageExceeded as exc:
            raise coverage_refusal(exc) from None
    out: list[RecognitionOut] = []
    for row, asked, result in rows:
        result = result or {}
        citations = [
            CitationOut(
                image_id=UUID(str(c["image_id"])),
                labels=[str(n) for n in c.get("labels", [])],
                author_name=authors.get(UUID(str(c["image_id"]))),
            )
            for c in result.get("citations", [])
            if UUID(str(c["image_id"])) in visible
        ]
        suggestions: list[dict[str, Any]] = list(result.get("suggestions", []))
        out.append(
            RecognitionOut(
                id=row.id,
                status=row.status,
                error=row.error,
                question=str(asked.get("question", "")),
                answer=result.get("answer"),
                grounded=bool(result.get("grounded", False)),
                vlm=result.get("vlm"),
                suggestions=[SuggestionOut.model_validate(s) for s in suggestions],
                citations=citations,
                created_at=row.created_at,
            )
        )
    return out

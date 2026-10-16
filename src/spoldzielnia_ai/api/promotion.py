"""``POST /notes/promote-to-wiki`` and ``GET /promotions/{id}`` (chunk 3.5)."""

from __future__ import annotations

import datetime as dt
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from spoldzielnia_ai.auth.deps import CurrentUser  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import PromotionStatus  # noqa: TC001 - pydantic resolves it
from spoldzielnia_ai.db.models import Promotion
from spoldzielnia_ai.db.rls import RlsSessionDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.wiki.promotion import MAX_SOURCES, PromotionRejected, request_promotion

if TYPE_CHECKING:
    from typing import Self

    from redis.asyncio import Redis

    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.notebook.uploads import ProcessingQueue

notes_router = APIRouter(prefix="/notes", tags=["promotion"])
router = APIRouter(prefix="/promotions", tags=["promotion"])


class PromoteIn(BaseModel):
    note_ids: list[UUID] = Field(default_factory=list[UUID], max_length=MAX_SOURCES)
    attachment_derivative_ids: list[UUID] = Field(
        default_factory=list[UUID], max_length=MAX_SOURCES
    )
    title_hint: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _sources(self) -> Self:
        if not self.note_ids and not self.attachment_derivative_ids:
            raise ValueError("at least one note or derivative is required")
        if len(set(self.note_ids)) + len(set(self.attachment_derivative_ids)) > MAX_SOURCES:
            raise ValueError(f"at most {MAX_SOURCES} sources")
        return self


class PromoteOut(BaseModel):
    promotion_id: UUID
    article_id: UUID


class PromotionOut(BaseModel):
    id: UUID
    status: PromotionStatus
    article_id: UUID
    error: str | None
    injection_kinds: list[str]
    pii_stats: dict[str, Any]
    created_at: datetime
    updated_at: datetime


async def _within_daily_limit(redis: Redis, user_id: UUID, limit: int) -> bool:
    key = f"promotions:{user_id}:{dt.datetime.now(dt.UTC):%Y%m%d}"
    count = int(await redis.incr(key))
    if count == 1:
        await redis.expire(key, 2 * 24 * 3600)
    return count <= limit


def _error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": {"error": code}}, status_code=status_code)


@notes_router.post(
    "/promote-to-wiki", status_code=status.HTTP_202_ACCEPTED, response_model=PromoteOut
)
async def promote_to_wiki(payload: PromoteIn, request: Request, user: CurrentUser) -> JSONResponse:
    """Turn selected notes and derivatives into a wiki draft, asynchronously (worker-core).
    Sources are checked through RLS (404 for anything not the caller's), derivatives must be
    processed, and quarantined ones confirmed (409)."""
    state = request.app.state
    settings: Settings = state.settings
    # Before anything is created; attempts count, so probing for others' ids is limited too.
    if not await _within_daily_limit(state.redis, user.id, settings.promotion.daily_limit):
        return _error("rate_limited", status.HTTP_429_TOO_MANY_REQUESTS)
    note_ids = list(dict.fromkeys(payload.note_ids))
    derivative_ids = list(dict.fromkeys(payload.attachment_derivative_ids))
    try:
        promotion_id, article_id = await request_promotion(
            state.session_factory,
            state.cipher,
            user.id,
            note_ids,
            derivative_ids,
            payload.title_hint,
        )
    except PromotionRejected as exc:
        return _error(exc.code, exc.status)
    queue: ProcessingQueue = state.processing_queue
    await queue.promotion_requested(promotion_id, user.id)
    body = PromoteOut(promotion_id=promotion_id, article_id=article_id)
    return JSONResponse(body.model_dump(mode="json"), status_code=status.HTTP_202_ACCEPTED)


@router.get("/{promotion_id}", response_model=PromotionOut)
async def get_promotion(promotion_id: UUID, session: RlsSessionDep) -> PromotionOut:
    promotion = await session.get(Promotion, promotion_id)
    if promotion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promotion not found")
    kinds = cast("list[str]", promotion.injection_flags.get("kinds", []))
    return PromotionOut(
        id=promotion.id,
        status=promotion.status,
        article_id=promotion.article_id,
        error=promotion.error,
        injection_kinds=kinds,
        pii_stats=dict(promotion.pii_stats),
        created_at=promotion.created_at,
        updated_at=promotion.updated_at,
    )

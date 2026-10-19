"""``/reviews``: the Expert Council's queue and decisions (chunk 4.2). EXPERT role + MFA.

A decision commits first (status, review record, audit, outbox rows), then its index operations
are applied synchronously: when the response is sent, an article that left CANONICAL is no
longer retrievable. An article that entered CANONICAL is queued for indexing.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from spoldzielnia_ai.auth.deps import CurrentUser, elevated_router
from spoldzielnia_ai.db.enums import ArticleStatus, ReviewDecision, Role
from spoldzielnia_ai.db.models import ArticleBlock, ExpertReview, WikiArticle
from spoldzielnia_ai.db.session import SessionFactory, get_session
from spoldzielnia_ai.vector.deps import VectorSyncDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.wiki.governance import REVIEWABLE
from spoldzielnia_ai.wiki.review import ReviewError, ReviewService

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings

router = elevated_router(Role.EXPERT, prefix="/reviews", tags=["reviews"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class QueueItemOut(BaseModel):
    article_id: UUID
    title: str
    status: ArticleStatus
    version: int
    review_alert: str | None
    status_changed_at: datetime
    approvals: int
    quorum: int
    is_own: bool  # the expert wrote part of it, so may not review it


class ReviewIn(BaseModel):
    decision: ReviewDecision
    comment: str | None = Field(default=None, max_length=5000)
    supersedes_id: UUID | None = None
    version: int = Field(ge=1, description="The article version the decision was made on")


class ReviewOut(BaseModel):
    article_id: UUID
    previous: ArticleStatus
    status: ArticleStatus
    version: int
    approvals: int
    quorum: int
    index_synced: bool


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/queue", response_model=list[QueueItemOut])
async def review_queue(
    request: Request, principal: CurrentUser, session: SessionDep
) -> list[QueueItemOut]:
    """Articles awaiting a decision, oldest first."""
    quorum = _settings(request).governance.review_quorum
    approvals = (
        select(func.count(distinct(ExpertReview.reviewer_id)))
        .where(
            ExpertReview.article_id == WikiArticle.id,
            ExpertReview.decision == ReviewDecision.APPROVE,
            ExpertReview.created_at >= WikiArticle.status_changed_at,
        )
        .scalar_subquery()
    )
    own = (
        select(ArticleBlock.id)
        .where(ArticleBlock.article_id == WikiArticle.id, ArticleBlock.author_id == principal.id)
        .exists()
    )
    rows = await session.execute(
        select(WikiArticle, approvals, own)
        .where(WikiArticle.status.in_(REVIEWABLE))
        .order_by(WikiArticle.status_changed_at, WikiArticle.id)
        .limit(200)
    )
    return [
        QueueItemOut(
            article_id=article.id,
            title=article.title,
            status=article.status,
            version=article.version,
            review_alert=article.review_alert,
            status_changed_at=article.status_changed_at,
            approvals=count,
            quorum=quorum,
            is_own=is_own,
        )
        for article, count, is_own in rows
    ]


@router.post("/{article_id}", response_model=ReviewOut)
async def review_article(
    article_id: UUID,
    payload: ReviewIn,
    request: Request,
    principal: CurrentUser,
    sync: VectorSyncDep,
) -> JSONResponse | ReviewOut:
    factory: SessionFactory = request.app.state.session_factory
    quorum = _settings(request).governance.review_quorum
    try:
        async with factory() as session, session.begin():
            result = await ReviewService(session, quorum).decide(
                article_id,
                principal.id,
                payload.decision,
                payload.comment,
                payload.version,
                payload.supersedes_id,
            )
            outbox = await sync.record(session, result.ops)
    except ReviewError as exc:
        return JSONResponse({"detail": {"error": exc.code}}, status_code=exc.status)
    synced = await sync.flush(outbox)
    return ReviewOut(
        article_id=result.article_id,
        previous=result.previous,
        status=result.status,
        version=result.version,
        approvals=result.approvals,
        quorum=result.quorum,
        index_synced=synced,
    )

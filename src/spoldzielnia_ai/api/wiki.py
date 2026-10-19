"""``/wiki``: reading articles and submitting them for review (chunk 4.2).

Members see CANONICAL articles (and SUPERSEDED ones, which link to their successor). Other
statuses are visible to the article's authors and to experts with MFA. Anyone else gets a 404,
never a 403 (no existence oracle).

A block's text is shown only while its consent is ACTIVE. SUSPENDED and REVOKED blocks are
rendered as withheld, except that an author still sees their own suspended text.

Canonical text shown to a member who is neither an author of the article nor a reviewer counts
against their coverage budget (5.2): the Wiki is not a way around the anti-distillation shield.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic resolves annotations at runtime
from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoldzielnia_ai.auth.deps import CurrentUser, Principal  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus, Role
from spoldzielnia_ai.db.models import ArticleBlock, User, WikiArticle
from spoldzielnia_ai.db.session import SessionFactory, get_session
from spoldzielnia_ai.shield.coverage import CoverageExceeded
from spoldzielnia_ai.shield.deps import CoverageDep, coverage_refusal
from spoldzielnia_ai.vector.deps import VectorSyncDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.wiki.review import ReviewError, ReviewService

router = APIRouter(prefix="/wiki", tags=["wiki"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

PUBLIC_STATUSES = frozenset({ArticleStatus.CANONICAL, ArticleStatus.SUPERSEDED})
WITHHELD = {
    ConsentStatus.SUSPENDED: "Treść wstrzymana przez autora",
    ConsentStatus.REVOKED: "Treść wycofana przez autora",
}


class AuthorOut(BaseModel):
    id: UUID
    display_name: str | None


class ArticleSummaryOut(BaseModel):
    id: UUID
    title: str
    status: ArticleStatus
    version: int
    last_verified_at: datetime | None
    authors: list[AuthorOut]


class BlockOut(BaseModel):
    id: UUID
    position: int
    author_id: UUID
    consent_status: ConsentStatus
    withheld: bool
    notice: str | None
    content: str | None


class ArticleOut(ArticleSummaryOut):
    supersedes_id: UUID | None
    superseded_by_id: UUID | None
    review_alert: str | None
    blocks: list[BlockOut]


class SubmitOut(BaseModel):
    id: UUID
    previous: ArticleStatus
    status: ArticleStatus
    version: int


def is_reviewer(principal: Principal) -> bool:
    return Role.EXPERT in principal.user.roles and principal.mfa_satisfied


async def _authors(session: AsyncSession, article_ids: list[UUID]) -> dict[UUID, list[AuthorOut]]:
    rows = await session.execute(
        select(ArticleBlock.article_id, User.id, User.display_name)
        .join(User, User.id == ArticleBlock.author_id)
        .where(ArticleBlock.article_id.in_(article_ids))
        .distinct()
        .order_by(ArticleBlock.article_id, User.id)
    )
    authors: dict[UUID, list[AuthorOut]] = {a: [] for a in article_ids}
    for article_id, user_id, display_name in rows:
        authors[article_id].append(AuthorOut(id=user_id, display_name=display_name))
    return authors


def render_block(block: ArticleBlock, viewer_id: UUID) -> BlockOut:
    visible = block.consent_status == ConsentStatus.ACTIVE or (
        block.consent_status == ConsentStatus.SUSPENDED and block.author_id == viewer_id
    )
    return BlockOut(
        id=block.id,
        position=block.position,
        author_id=block.author_id,
        consent_status=block.consent_status,
        withheld=block.consent_status != ConsentStatus.ACTIVE,
        notice=WITHHELD.get(block.consent_status),
        content=block.content if visible else None,
    )


@router.get("/articles", response_model=list[ArticleSummaryOut])
async def list_articles(
    _user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ArticleSummaryOut]:
    """Canonical articles, most recently verified first."""
    articles = list(
        await session.scalars(
            select(WikiArticle)
            .where(WikiArticle.status == ArticleStatus.CANONICAL)
            .order_by(WikiArticle.last_verified_at.desc().nulls_last(), WikiArticle.id)
            .limit(limit)
            .offset(offset)
        )
    )
    authors = await _authors(session, [a.id for a in articles])
    return [
        ArticleSummaryOut(
            id=a.id,
            title=a.title,
            status=a.status,
            version=a.version,
            last_verified_at=a.last_verified_at,
            authors=authors[a.id],
        )
        for a in articles
    ]


@router.get("/articles/{article_id}", response_model=ArticleOut)
async def get_article(
    article_id: UUID, principal: CurrentUser, session: SessionDep, coverage: CoverageDep
) -> ArticleOut:
    article = await session.get(WikiArticle, article_id)
    if article is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    authors = (await _authors(session, [article_id]))[article_id]
    is_author = principal.id in {a.id for a in authors}
    if article.status not in PUBLIC_STATUSES and not is_author and not is_reviewer(principal):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    blocks = list(
        await session.scalars(
            select(ArticleBlock)
            .where(ArticleBlock.article_id == article_id)
            .order_by(ArticleBlock.position)
        )
    )
    if article.status == ArticleStatus.CANONICAL and not is_author and not is_reviewer(principal):
        # Reading canonical text counts against the coverage budget like RAG retrieval (5.2).
        shown = [b.id for b in blocks if b.consent_status == ConsentStatus.ACTIVE and b.content]
        try:
            await coverage.admit(principal.id, shown, [article.id])
        except CoverageExceeded as exc:
            raise coverage_refusal(exc) from None
    return ArticleOut(
        id=article.id,
        title=article.title,
        status=article.status,
        version=article.version,
        last_verified_at=article.last_verified_at,
        authors=authors,
        supersedes_id=article.supersedes_id,
        superseded_by_id=article.superseded_by_id,
        review_alert=article.review_alert if is_author or is_reviewer(principal) else None,
        blocks=[render_block(b, principal.id) for b in blocks],
    )


@router.post("/articles/{article_id}/submit", response_model=SubmitOut)
async def submit_article(
    article_id: UUID, request: Request, principal: CurrentUser, sync: VectorSyncDep
) -> JSONResponse | SubmitOut:
    """DRAFT → IN_REVIEW by an author, or REVISION_REQUIRED → IN_REVIEW by an author or an
    expert (with MFA)."""
    factory: SessionFactory = request.app.state.session_factory
    try:
        async with factory() as session, session.begin():
            result = await ReviewService(session).submit(
                article_id, principal.id, is_reviewer(principal)
            )
            outbox = await sync.record(session, result.ops)
    except ReviewError as exc:
        return JSONResponse({"detail": {"error": exc.code}}, status_code=exc.status)
    await sync.flush(outbox)
    return SubmitOut(
        id=result.article_id,
        previous=result.previous,
        status=result.status,
        version=result.version,
    )

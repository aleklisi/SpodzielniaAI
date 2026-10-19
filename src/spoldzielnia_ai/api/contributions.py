"""``/me/contributions``: the creator's Exit Strategy (chunk 4.3, ARCHITECTURE §3).

A member acts only on blocks they authored: the scope is built into the query, so another
author's article id simply matches nothing. Without ``article_ids`` an action applies to all
of the member's contributions (gap G18).

Order of operations (gap G17):
1. One transaction: lock the articles and blocks, change consent (wipe text on revoke),
   cascade to co-authored articles, audit. Commit.
2. Apply the Qdrant operations synchronously (``wait=True``, bounded timeout).
3. Respond. If step 2 failed, the response still reports success with ``index_synced=false``:
   Postgres is the source of truth, and retrieval re-validates every hit against it.

Revoking is irreversible. It needs ``confirm: "REVOKE"`` and a login at most
``governance.revoke_max_auth_age_seconds`` old (otherwise 403 with a step-up link).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Literal
from uuid import UUID  # noqa: TC003 - FastAPI/pydantic resolve annotations at runtime

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from spoldzielnia_ai.auth.deps import CurrentUser, Principal  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.core.logging import get_logger
from spoldzielnia_ai.db.enums import ArticleStatus, ConsentStatus  # noqa: TC001 - pydantic
from spoldzielnia_ai.db.models import ArticleBlock, WikiArticle
from spoldzielnia_ai.vector.deps import VectorSyncDep  # noqa: TC001 - FastAPI resolves it
from spoldzielnia_ai.vector.repositories import CanonicalVectorRepo
from spoldzielnia_ai.wiki.consent import ConsentAction, ConsentService

if TYPE_CHECKING:
    from spoldzielnia_ai.core.settings import Settings
    from spoldzielnia_ai.db.session import SessionFactory
    from spoldzielnia_ai.vector.sync import VectorSync

router = APIRouter(prefix="/me/contributions", tags=["contributions"])
log = get_logger(__name__)

MAX_ARTICLES = 500


class ScopeIn(BaseModel):
    article_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=MAX_ARTICLES)


class RevokeIn(ScopeIn):
    confirm: Literal["REVOKE"]


class ConsentOut(BaseModel):
    action: ConsentAction
    blocks_affected: int
    articles: list[UUID]
    degraded: list[UUID]
    withdrawn: list[UUID]
    index_synced: bool


class ContributionBlockOut(BaseModel):
    id: UUID
    position: int
    consent_status: ConsentStatus


class ContributionOut(BaseModel):
    article_id: UUID
    title: str
    status: ArticleStatus
    co_authored: bool
    blocks: list[ContributionBlockOut]


@router.get("", response_model=list[ContributionOut])
async def list_contributions(request: Request, principal: CurrentUser) -> list[ContributionOut]:
    """Every article the member wrote a block of, with each of their blocks' consent."""
    factory: SessionFactory = request.app.state.session_factory
    async with factory() as session:
        mine = list(
            await session.scalars(
                select(ArticleBlock)
                .where(ArticleBlock.author_id == principal.id)
                .order_by(ArticleBlock.article_id, ArticleBlock.position)
            )
        )
        article_ids = sorted({b.article_id for b in mine})
        articles = {
            a.id: a
            for a in await session.scalars(
                select(WikiArticle).where(WikiArticle.id.in_(article_ids))
            )
        }
        co_authored = set(
            await session.scalars(
                select(ArticleBlock.article_id)
                .where(
                    ArticleBlock.article_id.in_(article_ids),
                    ArticleBlock.author_id != principal.id,
                )
                .distinct()
            )
        )
    return [
        ContributionOut(
            article_id=article_id,
            title=articles[article_id].title,
            status=articles[article_id].status,
            co_authored=article_id in co_authored,
            blocks=[
                ContributionBlockOut(id=b.id, position=b.position, consent_status=b.consent_status)
                for b in mine
                if b.article_id == article_id
            ],
        )
        for article_id in article_ids
    ]


async def _change(
    action: ConsentAction,
    scope: ScopeIn,
    request: Request,
    principal: Principal,
    sync: VectorSync,
) -> ConsentOut:
    started = time.perf_counter()
    state = request.app.state
    factory: SessionFactory = state.session_factory
    article_ids = list(dict.fromkeys(scope.article_ids)) if scope.article_ids else None
    count = CanonicalVectorRepo(state.qdrant).count
    async with factory() as session, session.begin():
        result = await ConsentService(session, count).change(action, principal.id, article_ids)
    synced = await sync.apply(result.ops)
    log.info(
        "consent.changed",
        action=action.value,
        blocks=result.blocks,
        degraded=len(result.degraded),
        index_synced=synced,
        propagation_seconds=round(time.perf_counter() - started, 4),
    )
    return ConsentOut(
        action=action,
        blocks_affected=result.blocks,
        articles=result.articles,
        degraded=result.degraded,
        withdrawn=result.withdrawn,
        index_synced=synced,
    )


@router.post("/suspend", response_model=ConsentOut)
async def suspend(
    payload: ScopeIn, request: Request, principal: CurrentUser, sync: VectorSyncDep
) -> ConsentOut:
    """Reversible: the blocks are hidden from the wiki and leave retrieval immediately."""
    return await _change(ConsentAction.SUSPEND, payload, request, principal, sync)


@router.post("/resume", response_model=ConsentOut)
async def resume(
    payload: ScopeIn, request: Request, principal: CurrentUser, sync: VectorSyncDep
) -> ConsentOut:
    """Undo a suspension. Articles degraded meanwhile need a new expert approval first."""
    return await _change(ConsentAction.RESUME, payload, request, principal, sync)


def _recently_authenticated(principal: Principal, settings: Settings) -> bool:
    auth_time = principal.session.auth_time
    max_age = settings.governance.revoke_max_auth_age_seconds
    return auth_time is not None and time.time() - auth_time <= max_age


@router.post("/revoke", response_model=ConsentOut)
async def revoke(
    payload: RevokeIn, request: Request, principal: CurrentUser, sync: VectorSyncDep
) -> JSONResponse | ConsentOut:
    """Irreversible: the text is deleted and the vectors are hard-deleted."""
    settings: Settings = request.app.state.settings
    if not _recently_authenticated(principal, settings):
        step_up = f"{settings.root_path}/auth/login?prompt=login"
        return JSONResponse(
            {"detail": {"error": "reauthentication_required", "step_up": step_up}},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return await _change(ConsentAction.REVOKE, payload, request, principal, sync)
